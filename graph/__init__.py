"""
graph/__init__.py

Public API for the graph module.
Import from here — not from submodules directly.

    from graph import GraphClient
    from graph.schema_definitions import SERVICE_REGISTRY, SOP_REGISTRY
"""

from graph.graph_client import GraphClient
from graph.schema_definitions import (
    NodeLabel,
    RelType,
    Prop,
    ServiceType,
    Criticality,
    ONLINE_BOUTIQUE_SERVICES,
    SERVICE_REGISTRY,
    SOP_REGISTRY,
    SOP_REGISTRY_BY_NAME,
    ServiceDefinition,
    SOPDefinition,
)

__all__ = [
    "GraphClient",
    "NodeLabel",
    "RelType",
    "Prop",
    "ServiceType",
    "Criticality",
    "ONLINE_BOUTIQUE_SERVICES",
    "SERVICE_REGISTRY",
    "SOP_REGISTRY",
    "SOP_REGISTRY_BY_NAME",
    "ServiceDefinition",
    "SOPDefinition",
]