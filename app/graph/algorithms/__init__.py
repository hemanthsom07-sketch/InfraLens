"""NetworkX-backed algorithm implementations. Each module here (plus
../core.py) is one of the only places in the project allowed to import
networkx directly — GraphEngine is the sole caller of everything in this
package, and translates raw node-id results into Node/ImpactReport
objects for its own callers.
"""
