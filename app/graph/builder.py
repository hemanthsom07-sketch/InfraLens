"""GraphBuilder: converts an InfrastructureModel into a Graph.

Three steps, in order — architecture doc §3:
1. Baseline mapping: every Component -> one Node, every Relationship -> one Edge.
2. Type refinement (refinement.py) applied while mapping each Node.
3. Inference (inference.py) applied once, over the full component list,
   after the baseline graph exists.

Never touches networkx directly — only Graph, Node, and Edge.
"""

from app.graph.core import Graph
from app.graph.inference import infer_all_edges
from app.graph.refinement import refine_node_type
from app.models.graph import Edge, Node
from app.models.ikm import InfrastructureModel


class GraphBuilder:
    """Builds a Graph from an InfrastructureModel. Stateless — safe to
    reuse a single instance across many builds."""

    def build(self, model: InfrastructureModel, *, infer: bool = True) -> Graph:
        """`infer=False` skips the inference step entirely, producing a
        faster "base graph" of only directly-parsed relationships —
        architecture doc §13's opt-in-enrichment trade-off. Defaults to
        True since inference is what makes the graph more useful than
        the IKM it's built from.
        """
        graph = Graph()

        for component in model.components:
            node_type = refine_node_type(component.technology, component.type, component.metadata)
            graph.add_node(
                Node(
                    id=component.id,
                    name=component.name,
                    node_type=node_type,
                    technology=component.technology,
                    metadata=component.metadata,
                )
            )

        for relationship in model.relationships:
            # Graph.add_edge() also checks this, but checking here first
            # avoids constructing an Edge object we're just going to
            # discard — see architecture doc §14, "dangling relationship
            # references": never crash on one, just skip it.
            if not graph.has_node(relationship.source) or not graph.has_node(relationship.target):
                continue
            graph.add_edge(
                Edge(
                    id=f"{relationship.source}--{relationship.relationship_type}-->{relationship.target}",
                    source=relationship.source,
                    target=relationship.target,
                    edge_type=relationship.relationship_type,
                    metadata={"origin": "parsed"},
                )
            )

        if infer:
            for edge in infer_all_edges(model.components):
                graph.add_edge(edge)

        return graph
