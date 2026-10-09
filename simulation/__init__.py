"""
Online Boutique simulation, independent health collection and physical faults.

Use simulation/docker-compose.yml for the supported 12-container deployment.
The injector never writes operational graph health or sends alerts. The collector
owns observations, stable incident identity and acknowledged delivery. Historical
Kubernetes/custom-service scaffolding is not the supported runtime.
"""

__version__ = "0.1.0"
