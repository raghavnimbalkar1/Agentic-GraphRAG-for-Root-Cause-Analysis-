"""Read-only consistency check of an existing candidate, never a new diagnosis."""

from datetime import datetime, timedelta

from agent.evidence_policy import _scope_views, assess_dependencies, screening_contract
from agent.investigation import InvestigationLimits
from core.diagnostics import FRESHNESS_SECONDS

RECHECK_POLICY = "candidate-consistency-v1"
RECHECK_LIMITS = InvestigationLimits(max_seconds=10)


def recheck_evidence(session, topology, original_report, original_cutoff):
    """Read every scoped component once. A changed fact requires new reasoning.

    This stage has its own declared read allowance, not extra diagnosis tools.
    It cannot choose another service, issue a proposal or grant an operation.
    """
    prior = original_report["initial_context"]
    proposal = original_report["result"]["proposal"]
    context = {**session.initial_context(), "topology": topology.model_dump(mode="json"),
               "registered_services": prior["registered_services"], "workflow_anchors": prior["workflow_anchors"]}
    scope = screening_contract(context)["required_scope"]
    result = {"policy": RECHECK_POLICY, "outcome": "rejected", "reason": None,
              "validation_target": proposal["service_id"], "condition": proposal["condition"],
              "required_scope": scope, "availability_cutoff": session.as_of.isoformat(),
              "feedback": [], "evidence_ids": [], "valid_until": None,
              "causal_attribution": False, "recovery_verified": False, "execution_authorized": False}

    def finish(reason):
        result["reason"] = reason
        result["read_budget"] = {"limits": session.limits.model_dump(), "session": session.snapshot()}
        return result

    if session.limits != RECHECK_LIMITS or len(scope) > session.remaining_tool_calls():
        return finish("recheck_budget_infeasible")
    if assess_dependencies(prior, original_report["turns"], proposal)["verdict"] != "candidate_supported":
        return finish("original_candidate_not_supported")
    old_scope, old_views, _, old_uncertain = _scope_views(prior, original_report["turns"])
    if scope != old_scope or old_uncertain:
        return finish("scope_changed")
    for name in scope:
        feedback = session.call({"tool": "component_window", "service_id": name, "window_seconds": 60})
        result["feedback"].append(feedback)
        if feedback["outcome"] != "ok":
            return finish("recheck_read_failed")
    turns = [{"actor": "candidate-recheck", "feedback": result["feedback"]}]
    _, views, seen, uncertain = _scope_views(context, turns)
    if set(scope) != seen or uncertain:
        return finish("incomplete_or_uncertain_scope")
    if any(datetime.fromisoformat(views[name]["signature"][0]) <= original_cutoff
           or views[name]["ids"] & old_views[name]["ids"] for name in scope):
        return finish("new_component_observations_required")
    if any(views[name]["signature"][2] != old_views[name]["signature"][2] for name in scope):
        return finish("runtime_instance_changed")
    if any(views[name]["status"] != old_views[name]["status"] for name in scope):
        return finish("scope_state_changed")
    # Rebind citations only for this boolean check; never publish a replacement proposal.
    check = {**proposal, "supporting_evidence": sorted(views[proposal["service_id"]]["ids"])}
    if assess_dependencies(context, turns, check)["verdict"] != "candidate_supported":
        return finish("current_scope_not_supported")
    until = min(datetime.fromisoformat(view["signature"][0]) + timedelta(seconds=FRESHNESS_SECONDS)
                for view in views.values())
    until = min(until, datetime.fromisoformat(context["symptom"]["measured_at"]) + timedelta(seconds=90))
    result.update(outcome="consistent", evidence_ids=sorted(identifier for view in views.values()
                  for identifier in view["ids"]), valid_until=until.isoformat())
    return finish("same_candidate_new_observations")
