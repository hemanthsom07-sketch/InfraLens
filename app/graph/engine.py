"""GraphEngine: the ONLY public interface to the Graph Engine.

Every future phase (AI Explanation, Security Analysis, Cloud Insights,
Frontend Visualization) and the API layer talk to this class exclusively
— nothing outside app/graph/ should ever import Graph, GraphBuilder, or
touch networkx. This module itself never imports networkx either; it
delegates every algorithm to app/graph/algorithms/*, passing along the
raw networkx graph those modules already know how to work with, and
translates their id-based results back into Node/Edge/ImpactReport
objects.
"""

from app.graph.algorithms import components as components_algo
from app.graph.algorithms import cycles as cycles_algo
from app.graph.algorithms import paths as paths_algo
from app.graph.algorithms import traversal as traversal_algo
from app.graph.builder import GraphBuilder
from app.graph.core import Graph
from app.graph.exceptions import NodeNotFoundError
from app.models.graph import GraphModel, ImpactReport, Node
from app.models.ikm import InfrastructureModel


class GraphEngine:
    """Facade over a built Graph. Construct via `from_infrastructure_model()`
    for the common case, or directly with an already-built Graph (handy
    in tests, against a hand-crafted fixture)."""

    def __init__(self, graph: Graph) -> None:
        self._graph = graph

    @classmethod
    def from_infrastructure_model(cls, model: InfrastructureModel, *, infer: bool = True) -> "GraphEngine":
        """Build + wrap in one call — GraphBuilder().build() then GraphEngine(...)."""
        return cls(GraphBuilder().build(model, infer=infer))

    # --- lookup ----------------------------------------------------------

    def get_node(self, node_id: str) -> Node | None:
        """The node with this id, or None if it doesn't exist."""
        return self._graph.get_node(node_id)

    def find_nodes(
        self,
        node_type: str | None = None,
        technology: str | None = None,
        name_pattern: str | None = None,
    ) -> list[Node]:
        """Filtered lookup — any combination of filters, all optional.
        `name_pattern` is a case-insensitive substring match on Node.name."""
        results = self._graph.nodes
        if node_type is not None:
            results = [n for n in results if n.node_type == node_type]
        if technology is not None:
            results = [n for n in results if n.technology == technology]
        if name_pattern is not None:
            pattern = name_pattern.lower()
            results = [n for n in results if pattern in n.name.lower()]
        return sorted(results, key=lambda n: n.id)

    # --- dependency traversal ---------------------------------------------

    def get_dependencies(self, node_id: str) -> list[Node]:
        """Everything `node_id` (transitively) depends on."""
        self._require_node(node_id)
        ids = traversal_algo.get_dependencies(self._graph.networkx_graph, node_id)
        return [self._graph.get_node(i) for i in ids]

    def get_dependents(self, node_id: str) -> list[Node]:
        """Everything that (transitively) depends on `node_id`."""
        self._require_node(node_id)
        ids = traversal_algo.get_dependents(self._graph.networkx_graph, node_id)
        return [self._graph.get_node(i) for i in ids]

    # --- algorithms --------------------------------------------------------

    def detect_cycles(self) -> list[list[Node]]:
        """Every dependency cycle in the graph — each a list of Nodes in
        cycle order. Empty list if there are none."""
        cycles = cycles_algo.detect_cycles(self._graph.networkx_graph)
        return [[self._graph.get_node(i) for i in cycle] for cycle in cycles]

    def topological_order(self) -> list[Node] | None:
        """A valid dependency order, or None if the graph has a cycle."""
        order = cycles_algo.topological_order(self._graph.networkx_graph)
        if order is None:
            return None
        return [self._graph.get_node(i) for i in order]

    def shortest_path(self, source_id: str, target_id: str) -> list[Node] | None:
        """The shortest node path from source to target (any edge type),
        or None if they aren't connected."""
        self._require_node(source_id)
        self._require_node(target_id)
        path = paths_algo.shortest_path(self._graph.networkx_graph, source_id, target_id)
        if path is None:
            return None
        return [self._graph.get_node(i) for i in path]

    def connected_components(self) -> list[list[Node]]:
        """Every weakly-connected cluster of nodes — a length-1 group
        means that node is structurally isolated from everything else."""
        groups = components_algo.connected_components(self._graph.networkx_graph)
        return [[self._graph.get_node(i) for i in group] for group in groups]

    def impact_analysis(self, node_id: str) -> ImpactReport:
        """What would be affected, directly or transitively, by a change
        to `node_id`."""
        self._require_node(node_id)
        direct_ids, transitive_ids = components_algo.impact_analysis(self._graph.networkx_graph, node_id)
        direct = [self._graph.get_node(i) for i in direct_ids]
        transitive = [self._graph.get_node(i) for i in transitive_ids]

        impact_by_type: dict[str, int] = {}
        for node in (*direct, *transitive):
            impact_by_type[node.node_type] = impact_by_type.get(node.node_type, 0) + 1

        return ImpactReport(
            target=self.get_node(node_id),
            direct_dependents=direct,
            transitive_dependents=transitive,
            total_impact_count=len(direct) + len(transitive),
            impact_by_type=impact_by_type,
        )

    # --- export --------------------------------------------------------------

    def to_model(self) -> GraphModel:
        """The full graph in wire format, ready for an API response."""
        nodes = sorted(self._graph.nodes, key=lambda n: n.id)
        edges = sorted(self._graph.edges, key=lambda e: e.id)
        has_cycles = len(cycles_algo.detect_cycles(self._graph.networkx_graph)) > 0
        return GraphModel(
            nodes=nodes,
            edges=edges,
            metadata={
                "node_count": len(nodes),
                "edge_count": len(edges),
                "has_cycles": has_cycles,
            },
        )

    # --- internal --------------------------------------------------------------

    def _require_node(self, node_id: str) -> None:
        if not self._graph.has_node(node_id):
            raise NodeNotFoundError(node_id)
