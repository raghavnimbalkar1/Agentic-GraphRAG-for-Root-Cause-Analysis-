"""Assess retained replanning reports against separate controls. No model or runtime."""

import argparse
from hashlib import sha256
from itertools import zip_longest
import json
from pathlib import Path
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.shadow_context import SUPPORTED_EVIDENCE_LAYOUTS
from agent.investigation import InvestigationLimits
from agent.shadow import ShadowLimits
from agent.shadow_protocol import INVESTIGATION_PROTOCOLS
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ServiceStatus
from eval.shadow_controls import assess_control
from eval.shadow_replay import _read_json, _write_new


class ExpectedProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal["proposal"]
    service_id: str
    condition: Literal["readiness_failure", "resource_pressure", "request_failure"]


class ExpectedAbstention(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal["abstained"]
    reason: Literal["insufficient_evidence", "unknown_condition", "unresolved_alternatives", "provider_unavailable"]


class EpochControl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    epoch: int = Field(strict=True, ge=1, le=4)
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected: Annotated[ExpectedProposal | ExpectedAbstention, Field(discriminator="outcome")]
    clue_service: str
    clue_status: ServiceStatus | None = None

    @model_validator(mode="after")
    def registered_clue(self):
        if self.clue_service not in REGISTRY.by_name:
            raise ValueError("A registered clue service is required")
        if isinstance(self.expected, ExpectedProposal) and self.expected.service_id != self.clue_service:
            raise ValueError("The proposal control requires a same-service clue")
        return self


class ReplanControls(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    kind: Literal["shadow-replan-controls"] = "shadow-replan-controls"
    profile: Literal["legacy", "two-epoch-v1"]
    evidence_layout: str = "legacy-v1"
    investigation_protocol: str = "model-directed-v1"
    implementation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_limits: InvestigationLimits
    shadow_limits: ShadowLimits
    epochs: tuple[EpochControl, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def complete_order(self):
        if self.evidence_layout not in SUPPORTED_EVIDENCE_LAYOUTS:
            raise ValueError("Unknown evidence layout")
        if self.investigation_protocol not in INVESTIGATION_PROTOCOLS:
            raise ValueError("Unknown investigation protocol")
        if self.profile == "two-epoch-v1" and len(self.epochs) != 2:
            raise ValueError("The two-epoch protocol requires both epoch controls")
        if [row.epoch for row in self.epochs] != list(range(1, len(self.epochs) + 1)):
            raise ValueError("Controls must include every consecutive epoch")
        return self


def implementation_digest(manifest):
    return sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _manifest_present(value):
    if (not isinstance(value, dict) or set(value) != {"source_sha256", "packages"}
            or not isinstance(value["packages"], dict) or not isinstance(value["source_sha256"], dict)
            or not 1 <= len(value["source_sha256"]) <= 128):
        return False
    return all(isinstance(key, str) and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest)
               for key, digest in value["source_sha256"].items())


def _last_proposal(report):
    turns = report.get("turns", [])
    for turn in reversed(turns):
        decision = turn.get("decision") or {}
        if decision.get("action") == "propose":
            target = (decision.get("hypothesis") or {}).get("service_id")
            reason = (turn.get("proposal_review") or {}).get("reason")
            reviewed = {"downstream_anomaly_unresolved", "competing_anomaly_unresolved", "declared_unresolved_evidence",
                "incomplete_or_uncertain_scope", "unsupported_condition", "candidate_outside_scope", "invalid_hypothesis",
                "condition_not_supported", "latest_target_evidence_required", "declared_scope_screen_passed"}
            return {"service_id": target if target in REGISTRY.by_name else None,
                    "screen_reason": reason if reason in reviewed else None}
    return None


def assess_replanning(snapshot, controls):
    """Control adherence at frozen cutoffs, not causal accuracy or live authorization."""
    controls = ReplanControls.model_validate(controls.model_dump(mode="json") if isinstance(controls, ReplanControls) else controls)
    if (not isinstance(snapshot, dict) or snapshot.get("kind") != "shadow-replanning"
            or type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1
            or not isinstance(snapshot.get("epochs"), list) or len(snapshot["epochs"]) > 4
            or any(not isinstance(row, dict) for row in snapshot["epochs"])):
        raise ValueError("A bounded replanning snapshot is required")
    matched_protocol = (snapshot.get("profile") == controls.profile
        and snapshot.get("evidence_layout", "legacy-v1") == controls.evidence_layout
        and snapshot.get("investigation_protocol", "model-directed-v1") == controls.investigation_protocol
        and _manifest_present(snapshot.get("implementation"))
        and implementation_digest(snapshot.get("implementation")) == controls.implementation_digest
        and snapshot.get("evidence_limits") == controls.evidence_limits.model_dump()
        and snapshot.get("shadow_limits") == controls.shadow_limits.model_dump()
        and snapshot.get("execution_authorized") is False)
    outcomes = []
    for index, (event, control) in enumerate(zip_longest(snapshot["epochs"], controls.epochs), 1):
        event = event or {}
        report = event.get("report") or {}
        invalid_shape = not isinstance(report, dict)
        if invalid_shape:
            report = {}
        parity = bool(control and event.get("epoch") == control.epoch and event.get("input_digest") == control.input_digest)
        score = {"control_passed": False, "explicit_policy_terminal": False}
        malformed = invalid_shape
        try:
            if control and report:
                score = assess_control(report, expected=control.expected.model_dump(), clue_service=control.clue_service,
                    clue_status=control.clue_status.value if control.clue_status is not None else None)
            last_proposal = _last_proposal(report)
        except (KeyError, TypeError, AttributeError):
            score, last_proposal, malformed = {"control_passed": False, "explicit_policy_terminal": False}, None, True
        screened = report.get("candidate_screened") is True
        layout_matches = (report.get("evidence_layout", "legacy-v1") == controls.evidence_layout
            and report.get("investigation_protocol", "model-directed-v1") == controls.investigation_protocol
            and report.get("context_profile") == "structured" and report.get("mode") == "shadow-only")
        passed = bool(matched_protocol and parity and not malformed and score["control_passed"]
            and layout_matches
            and report.get("execution_authorized") is False and report.get("usage_complete") is True
            and (not isinstance(control.expected, ExpectedProposal) or
                 (screened and report.get("screening_policy") == "dependency-screen-v1")))
        outcomes.append({"epoch": index, "expected_epoch_present": control is not None,
            "actual_epoch_present": bool(event), "input_digest_matches": parity,
            "diagnostic_control_passed": passed, "score": score, "malformed_report": malformed,
            "candidate_screened": screened, "last_policy_proposal": last_proposal,
            "report_layout_matches": layout_matches,
            "guard_fallback": bool(report and not score["explicit_policy_terminal"]),
            "usage_complete": report.get("usage_complete") is True})
    complete = (len(snapshot["epochs"]) == len(controls.epochs)
        and [row.get("epoch") for row in snapshot["epochs"]] == [row.epoch for row in controls.epochs])
    return {"schema_version": 1, "kind": "shadow-replan-assessment", "protocol_matches": matched_protocol,
        "all_epochs_recorded": complete, "epochs": outcomes,
        "diagnostic_control_passed": complete and matched_protocol and all(row["diagnostic_control_passed"] for row in outcomes),
        "current_candidate_recorded": snapshot.get("current_candidate") is not None,
        "current_freshness_independently_verified": False, "assessment_model_calls": 0,
        "causal_accuracy_measured": False, "recovery_verified": False, "execution_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--controls", type=Path, required=True, help="Separate post-run controls; never policy input")
    parser.add_argument("--output", type=Path, required=True, help="New owner-only assessment; no overwrite")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("Use a new assessment path")
    try:
        _write_new(args.output.with_name(args.output.name + ".started.json"),
            {"kind": "shadow-replan-assessment-start", "assessment_model_calls": 0, "execution_authorized": False})
        result = assess_replanning(_read_json(args.report), _read_json(args.controls))
        _write_new(args.output, result)
    except (ValueError, OSError, TypeError, KeyError):
        print(json.dumps({"outcome": "rejected", "reason": "assessment_unavailable_or_invalid", "assessment_model_calls": 0}))
        return 1
    print(json.dumps({key: result[key] for key in
        ("all_epochs_recorded", "protocol_matches", "diagnostic_control_passed", "assessment_model_calls", "execution_authorized")}))
    return 0 if result["diagnostic_control_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
