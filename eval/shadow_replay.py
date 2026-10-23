"""Freeze sanitized shadow inputs and replay policies without live probes or repairs."""

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from agent.investigation import InvestigationLimits, InvestigationSession
from agent.shadow import DependencySnapshot, ShadowLimits, ShadowRunner
from core.log_signals import LogObservation
from core.registry import DEFAULT_DEPLOYMENT as REGISTRY
from core.schemas import ObservationRecord, SymptomObservation
from eval.baselines.investigation import FixedProbePolicy

MAX_ROWS = 4096
MAX_BYTES = 8 * 1024 * 1024
MEASUREMENTS = frozenset({"maxmemory", "clients", "expires", "bytes", "cpu_percent", "seconds"})
SOURCE_FILES = ("agent/investigation.py", "agent/shadow.py", "agent/shadow_provider.py", "agent/evidence_policy.py",
                "agent/shadow_context.py", "agent/shadow_protocol.py", "agent/shadow_rate.py",
                "core/diagnostics.py", "core/log_signals.py", "core/registry.py", "core/schemas.py",
                "core/timestamps.py", "core/deployments/online_boutique.json",
                "eval/baselines/investigation.py", "eval/shadow_replay.py", "eval/shadow_preflight.py", "eval/shadow_diagnose.py")


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _decode(data):
    def invalid_constant(_):
        raise ValueError("Non-finite JSON")

    return json.loads(data, object_pairs_hook=_object, parse_constant=invalid_constant)


def _normalized(kind, payload):
    """Export only reviewed fields; never retain arbitrary component probe text."""
    if not isinstance(payload, dict) or not {"evidence_id", "collected_at"} <= payload.keys():
        raise ValueError("Explicit observation identity and collection time are required")
    model = {"component": ObservationRecord, "symptom": SymptomObservation, "log": LogObservation}[kind]
    record = model.model_validate(payload)
    if (record.environment_id != REGISTRY.environment_id or record.registry_digest != REGISTRY.digest
            or record.registry_revision != REGISTRY.revision or record.service_id not in REGISTRY.by_name):
        raise ValueError("Evidence does not match the reviewed deployment")
    if kind == "component":
        if (record.schema_version != 2 or re.fullmatch(r"OBS-[a-f0-9]{32}", record.evidence_id) is None
                or (record.instance_id is not None and re.fullmatch(r"[a-f0-9]{64}", record.instance_id) is None)):
            raise ValueError("Canonical version 2 component identities are required")
        measurements = {key: value for key, value in record.measurements.items() if key in MEASUREMENTS}
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or value < 0 for value in measurements.values()):
            raise ValueError("Invalid reviewed measurement")
        record.detail = f"Built-in health probe: {record.status}"
        record.raw_artifact_ref = None
        record.measurements = measurements
    elif kind == "symptom" and re.fullmatch(r"SYMOBS-[a-f0-9]{32}", record.evidence_id) is None:
        raise ValueError("Canonical symptom identity is required")
    normalized = record.model_dump(mode="json")
    if len(_encoded(normalized)) > 8192:
        raise ValueError("Evidence exceeds its payload bound")
    return normalized


class FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["component", "symptom", "log"]
    stored_at: float = Field(strict=True, allow_inf_nan=False)
    payload: dict

    @model_validator(mode="after")
    def sanitized_record(self):
        if self.payload != _normalized(self.kind, self.payload):
            raise ValueError("Frozen evidence must contain only canonical sanitized fields")
        return self


class FrozenInputs(BaseModel):
    """Inputs only: no expected target, result label, episode state or model setting."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    kind: Literal["frozen-shadow-inputs"] = "frozen-shadow-inputs"
    source_kind: Literal["synthetic", "retained-observation"]
    as_of: AwareDatetime
    symptom_evidence_id: str = Field(pattern=r"^SYMOBS-[a-f0-9]{32}$")
    topology: DependencySnapshot
    records: tuple[FrozenRecord, ...] = Field(min_length=1, max_length=MAX_ROWS)

    @model_validator(mode="after")
    def consistent_inputs(self):
        cutoff = self.as_of.timestamp()
        if self.as_of > datetime.now(timezone.utc) or self.topology.captured_at > self.as_of:
            raise ValueError("Inputs or topology were unavailable at the cutoff")
        identifiers, symptoms = [], []
        for row in self.records:
            payload = row.payload
            identifiers.append(payload["evidence_id"])
            measured = datetime.fromisoformat(payload["measured_at"]).timestamp()
            collected = datetime.fromisoformat(payload["collected_at"]).timestamp()
            if not cutoff - 900 <= measured <= collected <= cutoff:
                raise ValueError("Evidence is outside the retained investigation window")
            if not cutoff - 86400 <= row.stored_at <= cutoff:
                raise ValueError("Evidence was not retained and available at the cutoff")
            if row.kind == "symptom":
                symptoms.append(payload)
        if identifiers != sorted(set(identifiers)):
            raise ValueError("Evidence IDs must be unique and sorted")
        symptoms.sort(key=lambda row: (datetime.fromisoformat(row["measured_at"]), row["evidence_id"]), reverse=True)
        trigger = next((row for row in symptoms[:20] if row["evidence_id"] == self.symptom_evidence_id), None)
        if (trigger is None or trigger["outcome"] != "failed"
                or cutoff - datetime.fromisoformat(trigger["measured_at"]).timestamp() > 90):
            raise ValueError("A recent readable failed transaction trigger is required")
        if len(_encoded(self.model_dump(mode="json"))) > MAX_BYTES - 256:
            raise ValueError("Frozen inputs exceed the bundle size bound")
        return self

    @property
    def digest(self):
        return sha256(_encoded(self.model_dump(mode="json"))).hexdigest()


def freeze_inputs(path: Path, *, symptom_evidence_id: str, as_of: datetime,
                  topology: DependencySnapshot, source_kind: str) -> FrozenInputs:
    """Take one read transaction; reject oversized/malformed scopes, never truncate."""
    if as_of.tzinfo is None or as_of > datetime.now(timezone.utc):
        raise ValueError("An aware non-future cutoff is required")
    cutoff = as_of.timestamp()
    names = sorted(REGISTRY.by_name)
    placeholders = ",".join("?" for _ in names)
    records, total = [], 0
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported evidence schema")
        cursor = db.execute(
            "SELECT id, service, kind, measured, stored, CASE WHEN length(CAST(payload AS BLOB))<=8192 "
            "THEN payload ELSE NULL END FROM evidence WHERE environment=? AND registry=? "
            f"AND service IN ({placeholders}) AND measured BETWEEN ? AND ? AND stored BETWEEN ? AND ? "
            "ORDER BY id LIMIT ?",
            (REGISTRY.environment_id, REGISTRY.digest, *names, cutoff - 900, cutoff,
             cutoff - 86400, cutoff, MAX_ROWS + 1))
        for identifier, service, kind, measured, stored, raw in cursor:
            if len(records) == MAX_ROWS or raw is None:
                raise ValueError("Evidence scope exceeds the row or payload bound")
            if kind not in {"component", "symptom", "log"}:
                raise ValueError("Unsupported evidence kind in the selected scope")
            payload = _normalized(kind, _decode(raw))
            if (payload["evidence_id"] != identifier or payload["service_id"] != service
                    or datetime.fromisoformat(payload["measured_at"]).timestamp() != measured):
                raise ValueError("Evidence columns do not match their payload")
            total += len(_encoded(payload))
            if total > MAX_BYTES:
                raise ValueError("Evidence scope exceeds the bundle byte bound")
            records.append(FrozenRecord(kind=kind, stored_at=stored, payload=payload))
    return FrozenInputs(source_kind=source_kind, as_of=as_of, symptom_evidence_id=symptom_evidence_id,
                        topology=topology, records=tuple(records))


def _read_json(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Input exceeds the bundle byte bound")
    return _decode(data)


def _write_new(path, value, *, bounded=False):
    data = _encoded(value) + b"\n"
    if bounded and len(data) > MAX_BYTES:
        raise ValueError("Input exceeds the bundle byte bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Private, exclusive files prevent an accidental overwrite of earlier evidence.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(data)


def save_inputs(path: Path, inputs: FrozenInputs):
    inputs = FrozenInputs.model_validate(inputs.model_dump(mode="json"))
    _write_new(path, {"sha256": inputs.digest, "inputs": inputs.model_dump(mode="json")}, bounded=True)


def load_inputs(path: Path) -> FrozenInputs:
    envelope = _read_json(path)
    if not isinstance(envelope, dict) or set(envelope) != {"sha256", "inputs"}:
        raise ValueError("Invalid input bundle envelope")
    inputs = FrozenInputs.model_validate(envelope["inputs"])
    if envelope["sha256"] != inputs.digest:
        raise ValueError("Input bundle digest mismatch")
    return inputs


@contextmanager
def materialize(inputs: FrozenInputs):
    """Use a disposable store, not EvidenceStore's wall-clock write/pruning path."""
    inputs = FrozenInputs.model_validate(inputs.model_dump(mode="json"))
    with TemporaryDirectory(prefix="shadow-replay-") as directory:
        path = Path(directory) / "evidence.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE evidence (id TEXT PRIMARY KEY, environment TEXT NOT NULL, "
                       "registry TEXT NOT NULL, service TEXT NOT NULL, kind TEXT NOT NULL, "
                       "measured REAL NOT NULL, stored REAL NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE INDEX evidence_window ON evidence(environment, registry, service, measured)")
            db.execute("PRAGMA user_version=1")
            for row in inputs.records:
                payload = row.payload
                db.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (payload["evidence_id"], REGISTRY.environment_id, REGISTRY.digest,
                            payload["service_id"], row.kind,
                            datetime.fromisoformat(payload["measured_at"]).timestamp(),
                            row.stored_at, _encoded(payload).decode()))
        yield path


def implementation_manifest():
    """Local source/dependency provenance, not an attestation of a clean Git revision."""
    root = Path(__file__).resolve().parents[1]
    packages = {}
    for name in ("pydantic", "langgraph", "langchain-openai", "openai", "tiktoken", "jsonschema"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {"source_sha256": {name: sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES},
            "packages": packages}


def run_frozen(inputs: FrozenInputs, driver, *, investigation_limits=None, shadow_limits=None,
               screening="provisional", context_profile="standard"):
    """An explicitly supplied model driver can send data externally. No driver is created here."""
    inputs = FrozenInputs.model_validate(inputs.model_dump(mode="json"))
    evidence_limits = InvestigationLimits.model_validate((investigation_limits or InvestigationLimits()).model_dump())
    decision_limits = ShadowLimits.model_validate((shadow_limits or ShadowLimits()).model_dump())
    implementation = implementation_manifest()
    with materialize(inputs) as path:
        session = InvestigationSession(path, symptom_evidence_id=inputs.symptom_evidence_id,
                                       as_of=inputs.as_of, limits=evidence_limits)
        report = ShadowRunner(session, inputs.topology, driver, limits=decision_limits, screening=screening,
                              context_profile=context_profile).run()
    return {"schema_version": 1, "kind": "frozen-shadow-run", "input_digest": inputs.digest,
            "source_kind": inputs.source_kind, "implementation": implementation,
            "investigation_limits": evidence_limits.model_dump(), "shadow_limits": decision_limits.model_dump(),
            "report": report, "causal_accuracy_measured": False, "execution_authorized": False}


def run_pair(inputs: FrozenInputs, comparator, *, investigation_limits=None, shadow_limits=None,
             screening="provisional", context_profile="standard"):
    """Fresh sessions, identical frozen data and ceilings; not a causal accuracy metric."""
    # Revalidate/copy before either driver runs, including caller-mutated nested fields.
    inputs = FrozenInputs.model_validate(inputs.model_dump(mode="json"))
    baseline = run_frozen(inputs, FixedProbePolicy(), investigation_limits=investigation_limits,
                          shadow_limits=shadow_limits, screening=screening, context_profile=context_profile)
    candidate = run_frozen(inputs, comparator, investigation_limits=investigation_limits,
                           shadow_limits=shadow_limits, screening=screening, context_profile=context_profile)
    same_context = baseline["report"]["initial_context_digest"] == candidate["report"]["initial_context_digest"]
    if not same_context or baseline["input_digest"] != candidate["input_digest"]:
        raise ValueError("Paired input parity was lost")
    return {"schema_version": 1, "kind": "paired-shadow-replay", "input_digest": inputs.digest,
            "initial_context_equal": same_context, "baseline": baseline, "comparator": candidate,
            "causal_accuracy_measured": False, "execution_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", help="Freeze a trusted local store and declared topology")
    freeze.add_argument("--store", type=Path, required=True)
    freeze.add_argument("--symptom", required=True)
    freeze.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    freeze.add_argument("--topology", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    synthetic = commands.add_parser("freeze-synthetic", help="Create a synthetic input bundle, not live evidence")
    from eval.shadow_controls import CASES, SYMPTOM_ID, fixture_topology, seed_case

    synthetic.add_argument("--case", choices=sorted(CASES), required=True)
    synthetic.add_argument("--output", type=Path, required=True)
    replay = commands.add_parser("replay", help="Run only the fixed policy; no provider or live probe")
    replay.add_argument("--inputs", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    replay.add_argument("--screened", action="store_true", help="Require declared-scope dependency evidence; still no repairs")
    replay.add_argument("--compact", action="store_true", help="Use lossless shared metadata in model-visible context")
    args = parser.parse_args()
    if args.command == "freeze":
        inputs = freeze_inputs(args.store, symptom_evidence_id=args.symptom, as_of=args.as_of,
            topology=DependencySnapshot.model_validate(_read_json(args.topology)), source_kind="retained-observation")
    elif args.command == "freeze-synthetic":
        with TemporaryDirectory() as directory:
            store = seed_case(Path(directory) / "evidence.sqlite", CASES[args.case][0])
            cutoff = datetime.now(timezone.utc)
            inputs = freeze_inputs(store.path, symptom_evidence_id=SYMPTOM_ID, as_of=cutoff,
                                   topology=fixture_topology(cutoff), source_kind="synthetic")
    else:
        inputs = load_inputs(args.inputs)
        result = run_frozen(inputs, FixedProbePolicy(), screening="dependency" if args.screened else "provisional",
                            context_profile="compact" if args.compact else "standard")
        _write_new(args.output, result)
        print(json.dumps({"input_digest": inputs.digest, "provider_attempts": 0,
                          "outcome": result["report"]["result"]["outcome"],
                          "candidate_screened": result["report"]["candidate_screened"], "execution_authorized": False}))
        return
    save_inputs(args.output, inputs)
    print(json.dumps({"input_digest": inputs.digest, "source_kind": inputs.source_kind,
                      "records": len(inputs.records), "provider_attempts": 0}))


if __name__ == "__main__":
    main()
