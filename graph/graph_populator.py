"""Retired telemetry-to-topology scaffold. Automatic discovery is future work."""


class GraphPopulator:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Automatic topology/SOP discovery is not implemented. "
            "Seed the catalog with graph.scripts.init_graph; "
            "simulation.telemetry_collector updates observed service health."
        )
