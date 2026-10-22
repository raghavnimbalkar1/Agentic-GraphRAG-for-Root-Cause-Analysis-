"""Topology-aware fixed probe policy using exactly the shadow runner's public inputs."""

from collections import deque
import json

from agent.shadow import DriverReply
from agent.investigation import READINESS_FAILURES, RESOURCE_ANOMALIES


class FixedProbePolicy:
    kind = "deterministic"

    def complete(self, messages, **_):
        view = json.loads(messages[-1]["content"])
        initial = view["initial"]
        edges = initial["topology"]["edges"]
        failed = next((step["step"] for step in initial["symptom"]["steps"]
                       if not step["success"] and step["step"] != "cleanup_cart"), "home")
        anchor = initial["workflow_anchors"].get(failed, "frontend")
        dependencies = {name: sorted({target for source, target in edges if source == name})
                        for name in initial["registered_services"]}
        queue, order = deque([anchor]), []
        while queue:
            service = queue.popleft()
            if service not in order:
                order.append(service)
                queue.extend(dependencies[service])
        results = {}
        for turn in view["turns"]:
            for feedback in turn.get("feedback", []):
                request = feedback.get("request") or {}
                if feedback.get("tool") == "component_window":
                    results[request["service_id"]] = feedback
        pending = [name for name in order if name not in results]
        if pending:
            if view["remaining_decisions"] == 0 or view["remaining_tool_calls"] == 0:
                return self._reply({"action": "abstain", "reason": "insufficient_evidence"})
            count = min(3, view["remaining_tool_calls"], len(pending))
            return self._reply({"action": "read", "requests": [
                {"tool": "component_window", "service_id": name, "window_seconds": 60} for name in pending[:count]]})
        candidates = {}
        for name in order:
            feedback = results[name]
            data = feedback.get("data")
            if feedback["outcome"] != "ok" or not data["summary"]["fresh_at_cutoff"]:
                return self._reply({"action": "abstain", "reason": "insufficient_evidence"})
            status = data["summary"]["status_at_window_end"]
            if status in READINESS_FAILURES:
                candidates[name] = ("readiness_failure", data["samples"][-1]["evidence_id"])
            elif status in RESOURCE_ANOMALIES:
                candidates[name] = ("resource_pressure", data["samples"][-1]["evidence_id"])
            elif status != "HEALTHY":
                return self._reply({"action": "abstain", "reason": "unknown_condition"})
        def reachable(start, target):
            seen, todo = set(), list(dependencies[start])
            while todo:
                node = todo.pop()
                if node == target:
                    return True
                if node not in seen:
                    seen.add(node)
                    todo.extend(dependencies[node])
            return False
        leaves = [name for name in candidates if not any(
            other != name and reachable(name, other) for other in candidates)]
        if len(leaves) != 1:
            return self._reply({"action": "abstain", "reason": "unresolved_alternatives" if candidates else "unknown_condition"})
        target = leaves[0]
        condition, identifier = candidates[target]
        return self._reply({"action": "propose", "hypothesis": {"service_id": target, "condition": condition,
                            "supporting_evidence": [identifier], "unresolved_alternatives": []}})

    @staticmethod
    def _reply(decision):
        return DriverReply(json.dumps(decision), {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
