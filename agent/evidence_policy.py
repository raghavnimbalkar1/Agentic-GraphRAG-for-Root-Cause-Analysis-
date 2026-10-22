"""Conservative declared-scope screening over public shadow feedback, not causal proof."""

from collections import deque
from datetime import datetime
import re

from agent.investigation import HypothesisProposal, READINESS_FAILURES, RESOURCE_ANOMALIES
from core.diagnostics import FRESHNESS_SECONDS
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY

SCREENING_POLICY = "dependency-screen-v1"


def _dependencies(context):
    return {name: {target for source, target in context["topology"]["edges"] if source == name}
            for name in context["registered_services"]}


def screening_contract(context):
    """Topology/workflow prior only. No component state or expected answer is read."""
    failed = next((step["step"] for step in context["symptom"]["steps"]
                   if not step["success"] and step["step"] != "cleanup_cart"), "home")
    anchor = context["workflow_anchors"].get(failed, "frontend")
    dependencies = _dependencies(context)
    queue, order = deque([anchor]), []
    while queue:
        name = queue.popleft()
        if name not in order:
            order.append(name)
            queue.extend(sorted(dependencies[name]))
    return {"policy": SCREENING_POLICY, "anchor": anchor, "required_scope": order,
            "coverage": "Fresh, known, identified component state for every service in declared scope",
            "condition_statuses": {"readiness_failure": sorted(READINESS_FAILURES),
                                   "resource_pressure": sorted(RESOURCE_ANOMALIES)},
            "request_failure": "A transaction/log symptom alone cannot establish a dependency candidate",
            "authority": "Screened candidate only; no causal attribution or execution permission"}


def _view(data, context):
    cutoff = datetime.fromisoformat(context["availability_cutoff"])
    if (data["registry_digest"] != REGISTRY.digest or data["registry_revision"] != REGISTRY.revision
            or data["environment_id"] != REGISTRY.environment_id
            or datetime.fromisoformat(data["evaluated_as_of"]) != cutoff
            or not data["summary"]["fresh_at_cutoff"] or data["summary"]["latest_conflict"]
            or not data["samples"]):
        return None
    latest = max(datetime.fromisoformat(row["measured_at"]) for row in data["samples"])
    rows = [row for row in data["samples"] if datetime.fromisoformat(row["measured_at"]) == latest]
    if not 0 <= (cutoff - latest).total_seconds() <= FRESHNESS_SECONDS:
        return None
    signatures = {(row["status"], row["quality"], row["instance_id"]) for row in rows}
    if len(signatures) != 1:
        return None
    status, quality, instance = signatures.pop()
    if (quality != "observed" or status == "UNKNOWN" or instance is None
            or re.fullmatch(r"[a-f0-9]{64}", instance) is None
            or any(re.fullmatch(r"OBS-[a-f0-9]{32}", row["evidence_id"]) is None for row in rows)):
        return None
    if any(not latest <= datetime.fromisoformat(row["collected_at"]) <= cutoff for row in rows):
        return None
    return {"signature": (latest.isoformat(), status, instance), "status": status,
            "ids": {row["evidence_id"] for row in rows}}


def assess_dependencies(context, turns, hypothesis=None):
    """Review only facts already shown to this policy. Never supply a replacement candidate."""
    scope = screening_contract(context)["required_scope"]
    views, seen, uncertain = {}, set(), set()
    for turn in turns:
        for feedback in turn.get("feedback", []):
            if (feedback.get("outcome") != "ok"
                    or feedback.get("tool") not in {"component_window", "resource_window"}):
                continue
            name = (feedback.get("request") or {}).get("service_id")
            if name not in scope:
                continue
            seen.add(name)
            try:
                data = feedback["data"]
                view = _view(data, context) if data["service_id"] == name else None
            except (ValueError, TypeError, KeyError):
                view = None
            if view is None:
                uncertain.add(name)
            elif name in views and views[name]["signature"] != view["signature"]:
                uncertain.add(name)
            else:
                if name in views:
                    view["ids"].update(views[name]["ids"])
                views[name] = view
    missing = [name for name in scope if name not in seen]
    unsupported = [name for name in scope if name in views and views[name]["status"] not in
                   READINESS_FAILURES | RESOURCE_ANOMALIES | {"HEALTHY"}]
    review = {"policy": SCREENING_POLICY, "required_scope": scope,
              "missing_services": missing, "uncertain_services": sorted(uncertain),
              "unsupported_services": unsupported,
              "coverage_complete": not missing and not uncertain,
              "verdict": "evidence_complete", "reason": "scope_observed",
              "causal_attribution": False, "execution_authorized": False}
    if missing or uncertain:
        review.update(verdict="more_evidence_required", reason="incomplete_or_uncertain_scope")
    elif unsupported:
        review.update(verdict="blocked", reason="unsupported_condition")
    if hypothesis is None:
        return review
    try:
        proposal = HypothesisProposal.model_validate(hypothesis)
        if any(name not in REGISTRY.by_name for name in [proposal.service_id, *proposal.unresolved_alternatives]):
            raise ValueError("Unknown proposal target")
    except (ValueError, TypeError):
        return {**review, "verdict": "blocked", "reason": "invalid_hypothesis"}
    if proposal.service_id not in scope:
        return {**review, "verdict": "blocked", "reason": "candidate_outside_scope"}
    if review["verdict"] != "evidence_complete":
        return review
    if proposal.unresolved_alternatives or proposal.contradicting_evidence:
        return {**review, "verdict": "blocked", "reason": "declared_unresolved_evidence"}
    target = proposal.service_id
    state = views[target]
    condition = ("readiness_failure" if state["status"] in READINESS_FAILURES else
                 "resource_pressure" if state["status"] in RESOURCE_ANOMALIES else None)
    if condition is None or proposal.condition != condition:
        return {**review, "verdict": "blocked", "reason": "condition_not_supported"}
    if not state["ids"].intersection(proposal.supporting_evidence):
        return {**review, "verdict": "blocked", "reason": "latest_target_evidence_required"}
    dependencies = _dependencies(context)

    def reachable(start, end):
        visited, pending = set(), list(dependencies[start])
        while pending:
            node = pending.pop()
            if node == end:
                return True
            if node not in visited:
                visited.add(node)
                pending.extend(dependencies[node])
        return False

    others = [name for name in scope if name != target and views[name]["status"] != "HEALTHY"]
    if any(reachable(target, name) for name in others):
        return {**review, "verdict": "blocked", "reason": "downstream_anomaly_unresolved"}
    if any(not reachable(name, target) for name in others):
        return {**review, "verdict": "blocked", "reason": "competing_anomaly_unresolved"}
    return {**review, "verdict": "candidate_supported", "reason": "declared_scope_screen_passed"}
