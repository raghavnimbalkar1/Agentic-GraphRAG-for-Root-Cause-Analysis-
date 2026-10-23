"""Native structured-output envelope; local decision validation remains authoritative."""

from copy import deepcopy


class ProviderFailure(RuntimeError):
    """Fixed categories and reviewed metadata only; never retain vendor error text."""

    def __init__(self, category, diagnostics):
        super().__init__(category)
        self.category, self.diagnostics = category, diagnostics

PLANNING = """
Plan reads before spending a terminal decision. A read may batch 1-3 requests.
When scope is incomplete, batch up to three still-missing component_window reads
per reply, without rereading the same window. Reserve one reply for propose or
abstain. This is a scheduling rule, not a target recommendation. If a required
service has UNKNOWN/unavailable evidence at this fixed cutoff, explicitly abstain
with insufficient_evidence; rereading cannot make missing evidence healthy.
Choose a candidate only after examining the declared dependencies: an upstream
symptom does not explain a failing downstream dependency. Independent competing
anomalies require abstention with unresolved_alternatives. Never hide uncertainty.
evidence_index repeats the already-read observations in a readable form. Cite
its exact observation IDs, not descriptions. HEALTHY observed dependencies are
not unresolved alternatives. A candidate with an anomalous dependency cannot be
the terminal dependency explanation. Examine all anomalies before choosing.
dependency_index is the same declared graph written as direct dependencies.
When unhealthy services lie on one dependency chain, they are not independent
alternatives: consider the downstream anomaly as the provisional explanation
for upstream symptoms. unresolved_alternatives means competing independent
branches, not multiple unhealthy nodes on that same chain. This is a structural
candidate, not a claim that declared edges prove real-world causality. You may
still abstain for insufficient evidence; no proposal authorizes a repair.
Return {"decision": <read, propose or abstain object>} using the provider schema.
For component_window use metric:null, end_at:null and window_seconds:60.
There is no free-text reasoning field. The local broker still validates every
target, metric, reference and candidate. No response authorizes remediation.
"""


def provider_schema(decision_schema, *, allowed_actions=("read", "propose", "abstain"), citation_ids=None):
    """Strict closed objects, all fields required, root object and nested anyOf."""
    def strict(node):
        if isinstance(node, list):
            return [strict(item) for item in node]
        if not isinstance(node, dict):
            return node
        result = {key: strict(value) for key, value in node.items()
                  if key not in {"title", "default", "discriminator"}}
        if "const" in result:
            result["enum"] = [result.pop("const")]
        if "oneOf" in result:
            result["anyOf"] = result.pop("oneOf")
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result

    schema = strict(deepcopy(decision_schema))
    if citation_ids is not None:
        for field in ("supporting_evidence", "contradicting_evidence"):
            schema["$defs"]["HypothesisProposal"]["properties"][field]["items"]["enum"] = sorted(citation_ids)
    names = {"read": "ReadDecision", "propose": "ProposeDecision", "abstain": "AbstainDecision"}
    if not allowed_actions or any(action not in names for action in allowed_actions):
        raise ValueError("Invalid native decision actions")
    choices = [{"$ref": f"#/$defs/{names[action]}"} for action in allowed_actions]
    needed = set()
    def references(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                if name not in needed:
                    needed.add(name)
                    references(schema["$defs"][name])
            for value in node.values():
                references(value)
        elif isinstance(node, list):
            for value in node:
                references(value)
    references(choices)
    return {"type": "object", "properties": {"decision": {"anyOf": choices}},
            "required": ["decision"], "additionalProperties": False,
            "$defs": {name: schema["$defs"][name] for name in sorted(needed)}}
