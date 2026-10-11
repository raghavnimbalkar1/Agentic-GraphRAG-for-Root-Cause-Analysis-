"""Information-parity control: full dependency edges as text, no Cypher."""

import json

from eval.baselines.zero_shot import ZeroShotBaseline, _build_system_prompt


class TopologyTextBaseline(ZeroShotBaseline):
    def __init__(self, edges: list[list[str]]) -> None:
        services = sorted({name for edge in edges for name in edge})
        super().__init__(services)
        self._edges = sorted(edges)
        self._system_prompt = _build_system_prompt(
            services, "Full dependency edges are supplied as text. [A,B] means A DEPENDS_ON B. No runbooks.")

    def _build_prompt(self, alert: dict) -> str:
        return super()._build_prompt(alert) + "\nDEPENDENCY EDGES:\n" + json.dumps(self._edges)
