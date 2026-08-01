"""Custom exceptions for the Graph Engine — same pattern as app/exceptions.py."""


class GraphError(Exception):
    """Base class for all Graph Engine errors."""


class NodeNotFoundError(GraphError):
    """Raised when a requested node id doesn't exist in the graph.

    Deliberately distinct from a method returning an empty result: an
    empty list of dependencies for a node that DOES exist is a normal,
    meaningful answer. Asking about a node id that was never in the graph
    at all is almost always a caller bug (a stale or mistyped id), so it
    fails loudly instead of silently looking identical to "no results".
    """

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(f"No node with id '{node_id}' exists in this graph.")
