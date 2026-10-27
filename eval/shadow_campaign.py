"""Freeze repeated synthetic controls and assess reports; no provider or runtime access."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.evidence_policy import assess_dependencies
from agent.shadow import Usage
from agent.shadow_budget import epoch_limits
from eval.shadow_controls import assess_control
from eval.shadow_preflight import RecordingPolicy
from eval.shadow_replan import ReplanningInvestigation
from eval.shadow_replan_assess import EpochControl
from eval.shadow_replay import _encoded, _read_json, _write_new, implementation_manifest, load_inputs, save_inputs


class CampaignCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")
    cohort: Literal["development", "prospective"]
    inputs: tuple[Path, ...] = Field(min_length=1, max_length=2)
    controls: tuple[EpochControl, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def ordered_epochs(self):
        if (len(self.inputs) != len(self.controls)
                or [row.epoch for row in self.controls] != list(range(1, len(self.inputs) + 1))):
            raise ValueError("Every input requires a consecutive separate epoch control")
        return self


class CampaignPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    kind: Literal["shadow-campaign-plan"] = "shadow-campaign-plan"
    repetitions: int = Field(strict=True, ge=1, le=3)
    cases: tuple[CampaignCase, ...] = Field(min_length=1, max_length=12)

    @field_validator("schema_version", mode="before")
    @classmethod
    def explicit_version(cls, value):
        if type(value) is not int:
            raise ValueError("An integer campaign schema version is required")
        return value

    @model_validator(mode="after")
    def distinct_cases(self):
        if len({row.case_id for row in self.cases}) != len(self.cases):
            raise ValueError("Case identities must be unique")
        digests = [tuple(control.input_digest for control in row.controls) for row in self.cases]
        if len(set(digests)) != len(digests):
            raise ValueError("Identical inputs belong in repetitions, not multiple case identities")
        return self


def _digest(value):
    return sha256(_encoded(value)).hexdigest()


def _schedule(plan):
    return [{"trial_id": f"{case.case_id}-r{repeat:02}", "case_id": case.case_id,
             "cohort": case.cohort, "repetition": repeat,
             "proposed_call_ceiling": 4 * len(case.inputs)}
            for repeat in range(1, plan.repetitions + 1) for case in plan.cases]


def _control_score(report, control):
    return assess_control(report, expected=control.expected.model_dump(mode="json"),
        clue_service=control.clue_service,
        clue_status=control.clue_status.value if control.clue_status is not None else None)


def prepare_campaign(plan, output: Path):
    """Copy sanitized inputs, freeze the schedule and preflight only the fixed policy."""
    plan = CampaignPlan.model_validate(plan.model_dump(mode="json") if isinstance(plan, CampaignPlan) else plan)
    implementation = implementation_manifest()
    loaded = {}
    for case in plan.cases:
        inputs = [load_inputs(path) for path in case.inputs]
        if any(value.source_kind != "synthetic" or value.schema_version != 1 or value.trace_records
               or value.digest != control.input_digest for value, control in zip(inputs, case.controls, strict=True)):
            raise ValueError("Only digest-matched, trace-free synthetic controls are supported")
        loaded[case.case_id] = inputs
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    _write_new(output / "prepare-start.json", {"kind": "offline-campaign-prepare", "new_model_calls": 0})
    normalized, preflights = [], []
    for case in plan.cases:
        inputs = loaded[case.case_id]
        paths = [Path(case.case_id) / f"epoch-{index:03}.json" for index in range(1, len(inputs) + 1)]
        for path, value in zip(paths, inputs, strict=True):
            save_inputs(output / path, value)
        controls = [row.model_dump(mode="json") for row in case.controls]
        _write_new(output / case.case_id / "controls.json", controls)
        policy = RecordingPolicy()
        controller = ReplanningInvestigation(inputs[0], profile="two-epoch-v1",
            evidence_layout="time-indexed-v1", investigation_protocol="bounded-coverage-v1")
        passed = []
        for value, control in zip(inputs, case.controls, strict=True):
            report = controller.run_epoch(value, policy).get("report")
            passed.append(bool(report and _control_score(report, control)["control_passed"]
                and (control.expected.outcome != "proposal" or report["candidate_screened"])))
        baseline = controller.snapshot()
        _write_new(output / case.case_id / "fixed.json", baseline)
        _write_new(output / case.case_id / "fixed-prompts.json", policy.prompts)
        normalized.append(case.model_copy(update={"inputs": tuple(paths)}).model_dump(mode="json"))
        preflights.append({"case_id": case.case_id, "passed": all(passed),
            "epoch_controls_passed": passed, "controls_sha256": _digest(controls),
            "fixed_sha256": _digest(baseline), "prompts_sha256": _digest(policy.prompts)})
    if implementation_manifest() != implementation:
        raise ValueError("Source changed during campaign preparation")
    frozen_plan = CampaignPlan(repetitions=plan.repetitions, cases=normalized)
    archive = {"sources": {name: (Path(__file__).resolve().parents[1] / name).read_text()
        for name in implementation["source_sha256"]}, "campaign_source": Path(__file__).read_text()}
    _write_new(output / "source-snapshot.json", archive)
    schedule = _schedule(frozen_plan)
    body = {"plan": frozen_plan.model_dump(mode="json"), "implementation": implementation,
        "campaign_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "archive_sha256": _digest(archive), "schedule": schedule, "fixed_preflights": preflights,
        "proposed_call_ceiling": sum(row["proposed_call_ceiling"] for row in schedule),
        "model_trial_authorized": False, "held_out_independently_verified": False,
        "new_model_calls": 0, "execution_authorized": False}
    _write_new(output / "campaign.json", {"sha256": _digest(body), "campaign": body})
    check_campaign(output)
    return body


def check_campaign(root: Path):
    """Require the original source, frozen matrix, controls and preflight records."""
    envelope = _read_json(root / "campaign.json")
    if set(envelope) != {"sha256", "campaign"} or envelope["sha256"] != _digest(envelope["campaign"]):
        raise ValueError("Campaign envelope changed")
    body = envelope["campaign"]
    plan = CampaignPlan.model_validate(body["plan"])
    archive = _read_json(root / "source-snapshot.json")
    if (body["implementation"] != implementation_manifest()
            or body["campaign_sha256"] != sha256(Path(__file__).read_bytes()).hexdigest()
            or body["archive_sha256"] != _digest(archive)
            or sha256(archive["campaign_source"].encode()).hexdigest() != body["campaign_sha256"]
            or any(sha256(archive["sources"][name].encode()).hexdigest() != digest
                for name, digest in body["implementation"]["source_sha256"].items())
            or body["schedule"] != _schedule(plan)
            or body["proposed_call_ceiling"] != sum(row["proposed_call_ceiling"] for row in _schedule(plan))
            or body["model_trial_authorized"] is not False or body["execution_authorized"] is not False
            or body["held_out_independently_verified"] is not False
            or type(body["new_model_calls"]) is not int or body["new_model_calls"] != 0):
        raise ValueError("Campaign source, schedule or authority changed")
    if len(body["fixed_preflights"]) != len(plan.cases):
        raise ValueError("Every frozen case requires a preflight record")
    for case, fixed in zip(plan.cases, body["fixed_preflights"], strict=True):
        paths = tuple(Path(case.case_id) / f"epoch-{index:03}.json" for index in range(1, len(case.inputs) + 1))
        if (case.inputs != paths or fixed["case_id"] != case.case_id
                or [value.model_dump(mode="json") for value in case.controls] != _read_json(root / case.case_id / "controls.json")
                or fixed["controls_sha256"] != _digest(_read_json(root / case.case_id / "controls.json"))
                or fixed["fixed_sha256"] != _digest(_read_json(root / case.case_id / "fixed.json"))
                or fixed["prompts_sha256"] != _digest(_read_json(root / case.case_id / "fixed-prompts.json"))):
            raise ValueError("Frozen case or preflight changed")
        for path, control in zip(paths, case.controls, strict=True):
            value = load_inputs(root / path)
            if value.digest != control.input_digest or value.source_kind != "synthetic" or value.schema_version != 1:
                raise ValueError("Frozen input changed")
    return body, plan


def _assess_snapshot(snapshot, case, body, inputs):
    evidence_limits, shadow_limits = epoch_limits("two-epoch-v1")
    events = snapshot["epochs"]
    if not isinstance(events, list) or len(events) != len(case.controls):
        raise ValueError("Every planned epoch requires one report")
    protocol = (snapshot["kind"] == "shadow-replanning" and type(snapshot["schema_version"]) is int
        and snapshot["schema_version"] == 1 and snapshot["profile"] == "two-epoch-v1"
        and snapshot["evidence_layout"] == "time-indexed-v1"
        and snapshot["investigation_protocol"] == "bounded-coverage-v1"
        and snapshot["source_kind"] == "synthetic" and snapshot["execution_authorized"] is False
        and snapshot["recovery_verified"] is False and snapshot["causal_attribution"] is False
        and snapshot["origin_input_digest"] == case.controls[0].input_digest
        and snapshot["implementation"] == body["implementation"]
        and snapshot["evidence_limits"] == evidence_limits.model_dump()
        and snapshot["shadow_limits"] == shadow_limits.model_dump())
    rows, calls, tokens, known, stop_required = [], 0, 0, True, False
    for index, (control, value) in enumerate(zip(case.controls, inputs, strict=True)):
        if index >= len(events) or not events[index].get("report"):
            rows.append({"epoch": index + 1, "passed": False, "reason": "missing_epoch_report"})
            known = False
            continue
        event, report = events[index], events[index]["report"]
        attempts = report["attempts"]
        count = report["provider_attempts"]
        if (type(count) is not int or not 0 <= count <= 4 or not isinstance(attempts, list)
                or count != len(attempts)):
            raise ValueError("Provider attempt accounting is inconsistent")
        calls += count
        epoch_tokens, usage_known = 0, True
        for number, attempt in enumerate(attempts, 1):
            if attempt["number"] != number or attempt["max_output_tokens"] != shadow_limits.max_output_tokens:
                raise ValueError("Attempt order or output ceiling changed")
            try:
                usage = Usage.model_validate(attempt["usage"])
                if usage.output_tokens > shadow_limits.max_output_tokens:
                    raise ValueError("Output token ceiling exceeded")
                epoch_tokens += usage.total_tokens
            except (ValueError, TypeError):
                usage_known = False
        tokens += epoch_tokens
        usage_matches = (usage_known and report["usage_complete"] is True
            and type(report["known_reported_tokens"]) is int
            and report["known_reported_tokens"] == epoch_tokens and epoch_tokens <= shadow_limits.max_reported_tokens)
        known = known and usage_matches
        stop_required = stop_required or (count > 0 and not usage_matches) or report["termination_reason"] == "provider_rate_limited"
        score = _control_score(report, control)
        initial = report["initial_context"]
        matched_inputs = (initial["availability_cutoff"] == value.as_of.isoformat()
            and initial["symptom"]["evidence_id"] == value.symptom_evidence_id
            and initial["topology"] == value.topology.model_dump(mode="json"))
        screen_recomputed = (control.expected.outcome != "proposal" or assess_dependencies(
            initial, report["turns"], report["result"].get("proposal"))["verdict"] == "candidate_supported")
        reads = report["session"]["calls_used"]
        passed = bool(protocol and event["epoch"] == control.epoch and event["input_digest"] == control.input_digest
            and event["execution_authorized"] is False and report["execution_authorized"] is False
            and report["driver_kind"] == "model" and report["context_profile"] == "structured"
            and report["mode"] == "shadow-only" and report["evidence_layout"] == "time-indexed-v1"
            and report["investigation_protocol"] == "bounded-coverage-v1"
            and report["screening_policy"] == "dependency-screen-v1"
            and 1 <= count <= 4 and type(reads) is int and 0 <= reads <= evidence_limits.max_tool_calls
            and score["control_passed"] and usage_matches and matched_inputs and screen_recomputed
            and (control.expected.outcome != "proposal" or report["candidate_screened"] is True))
        rows.append({"epoch": index + 1, "passed": passed, "score": score,
            "recorded_provider_attempts": count, "known_reported_tokens": epoch_tokens,
            "usage_complete": usage_matches, "frozen_context_matches": matched_inputs,
            "evidence_screen_recomputed": screen_recomputed})
    known = (known and type(snapshot["incident_budget"]["known_reported_tokens"]) is int
        and snapshot["incident_budget"]["known_reported_tokens"] == tokens)
    return {"passed": protocol and len(events) == len(case.controls) and known and all(row["passed"] for row in rows),
        "epochs": rows, "usage_complete": known, "recorded_provider_attempts": calls,
        "known_reported_tokens": tokens, "reported_stop_required": stop_required, "reason": "scored"}


def assess_campaign(root: Path, reports):
    """Score all declared slots, including missing/invalid ones; never prove SDK origin."""
    body, plan = check_campaign(root)
    if (not isinstance(reports, dict) or any(not isinstance(key, str) or not isinstance(value, (str, Path))
            for key, value in reports.items()) or set(reports) - {row["trial_id"] for row in body["schedule"]}):
        raise ValueError("Reports must map only predeclared trial identities to local files")
    cases = {case.case_id: case for case in plan.cases}
    rows, hashes = [], {}
    for trial in body["schedule"]:
        row = {**trial, "status": "not_run", "passed": False, "reason": "report_not_supplied",
               "recorded_provider_attempts": 0, "known_reported_tokens": 0, "usage_complete": False}
        if trial["trial_id"] in reports:
            try:
                snapshot = _read_json(Path(reports[trial["trial_id"]]))
                digest = _digest(snapshot)
                hashes.setdefault(digest, []).append(len(rows))
                case = cases[trial["case_id"]]
                values = [load_inputs(root / path) for path in case.inputs]
                row.update(_assess_snapshot(snapshot, case, body, values), status="recorded",
                           report_sha256=digest)
            except (ValueError, OSError, TypeError, KeyError, AttributeError):
                row.update(status="invalid", reason="invalid_or_unavailable_report")
        rows.append(row)
    duplicate = False
    for indices in hashes.values():
        if len(indices) > 1:
            duplicate = True
            for index in indices:
                rows[index].update(passed=False, reason="duplicate_report_not_an_independent_repeat")
    complete = all(row["status"] == "recorded" for row in rows)
    stop_seen, stop_respected = False, True
    for row in rows:
        if stop_seen and row["recorded_provider_attempts"]:
            stop_respected = False
        stop_seen = stop_seen or row.get("reported_stop_required", False)
    fixed_passed = all(row["passed"] for row in body["fixed_preflights"])
    return {"schema_version": 1, "kind": "offline-shadow-campaign-assessment",
        "campaign_digest": _digest(body), "trials": rows,
        "all_reports_supplied": len(reports) == len(rows), "all_reports_valid": complete,
        "fixed_preflights_passed": fixed_passed, "reported_provider_stop_respected": stop_respected,
        "all_planned_trials_passed": complete and fixed_passed and not duplicate and stop_respected
            and all(row["passed"] for row in rows),
        "cohorts": {cohort: {"planned": sum(row["cohort"] == cohort for row in rows),
            "passed": sum(row["cohort"] == cohort and row["passed"] for row in rows)}
            for cohort in ("development", "prospective")},
        "duplicate_reports_present": duplicate, "usage_complete": complete and not duplicate
            and all(row["usage_complete"] for row in rows),
        "known_reported_tokens": sum(row["known_reported_tokens"] for row in rows)
            if not duplicate else None,
        "proposed_call_ceiling": body["proposed_call_ceiling"], "assessment_model_calls": 0,
        "provider_origin_independently_verified": False, "held_out_independently_verified": False,
        "causal_accuracy_measured": False, "live_freshness_verified": False,
        "recovery_verified": False, "execution_authorized": False, "main_merge_allowed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Offline fixed-policy preflight and immutable trial schedule")
    prepare.add_argument("--plan", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True, help="New private directory; never reuse")
    assess = commands.add_parser("assess", help="Offline scoring of supplied reports, including missing trials")
    assess.add_argument("--campaign", type=Path, required=True)
    assess.add_argument("--reports", type=Path, required=True, help="JSON mapping trial IDs to snapshot paths")
    assess.add_argument("--output", type=Path, required=True, help="New owner-only report; cannot overwrite")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare_campaign(_read_json(args.plan), args.output)
            print(json.dumps({"prepared": True, "planned_trials": len(result["schedule"]),
                "proposed_call_ceiling": result["proposed_call_ceiling"], "new_model_calls": 0,
                "model_trial_authorized": False}))
            return 0 if all(row["passed"] for row in result["fixed_preflights"]) else 2
        result = assess_campaign(args.campaign, _read_json(args.reports))
        _write_new(args.output, result)
        print(json.dumps({key: result[key] for key in ("all_reports_valid", "all_planned_trials_passed",
            "assessment_model_calls", "execution_authorized")}))
        return 0 if result["all_planned_trials_passed"] else 2
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        print(json.dumps({"outcome": "rejected", "reason": "campaign_unavailable_or_invalid", "new_model_calls": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
