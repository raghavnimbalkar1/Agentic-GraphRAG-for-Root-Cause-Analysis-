"""Explicit opt-in Groq adapter. Importing this module loads no settings or credentials."""

from agent.shadow import DriverReply, _ids, decision_schema
from agent.shadow_context import unpack_view
from agent.shadow_protocol import ProviderFailure, provider_schema
from agent.shadow_rate import RollingTokenAdmission
from core.llm_usage import token_usage
from langsmith import tracing_context
import json


def safe_provider_failure(error, schema=None):
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    data = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(data, dict):
        data = {}
    codes = {"json_validate_failed", "invalid_request_error", "context_length_exceeded",
             "rate_limit_exceeded", "model_not_found", "invalid_api_key"}
    params = {"response_format", "messages", "max_tokens", "max_completion_tokens", "reasoning_effort", "model"}
    message = str(data.get("message", "")).lower()
    generated = data.get("failed_generation")
    diagnostics = {"http_status": status if type(status) is int else None,
        "code": data.get("code") if data.get("code") in codes else "unclassified",
        "parameter": data.get("param") if data.get("param") in params else None,
        "mentions_schema": "schema" in message, "mentions_json": "json" in message,
        "mentions_tokens": "token" in message, "mentions_length": "length" in message,
        "generated_bytes": len(generated.encode()) if isinstance(generated, str) else None,
        "generation_has_envelope": '"decision"' in generated if isinstance(generated, str) else None}
    if isinstance(generated, str) and schema is not None:
        try:
            from jsonschema import Draft202012Validator

            parsed = json.loads(generated)
            errors = list(Draft202012Validator(schema).iter_errors(parsed))
            leaves = []
            def collect(error):
                if error.context:
                    for child in error.context:
                        collect(child)
                else:
                    leaves.append({"rule": error.validator, "schema_path": list(error.absolute_schema_path)})
            for item in errors:
                collect(item)
            diagnostics["local_schema_valid"] = not errors
            diagnostics["schema_failures"] = leaves[:12]
        except (ValueError, TypeError, ImportError):
            diagnostics["generation_diagnostics_unavailable"] = True
    category = {400: "provider_bad_request", 401: "provider_authentication_failed",
                403: "provider_access_denied", 429: "provider_rate_limited"}.get(status, "provider_unavailable")
    return ProviderFailure(category, diagnostics)


class GroqShadowDriver:
    kind = "model"

    def __init__(self, model, *, structured=False, rate_limiter=None):
        if model.max_retries != 0:
            raise ValueError("Shadow provider retries must be disabled")
        self.model = model
        self.structured_decisions = structured
        self._tokenizer = None
        self._rate = (rate_limiter or RollingTokenAdmission(minimum_interval=0)) if structured else None
        self._ticket = None
        if structured:
            if model.model_name not in {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}:
                raise ValueError("Structured shadow driver requires a reviewed GPT-OSS model")
            import tiktoken

            self._tokenizer = tiktoken.get_encoding("o200k_harmony")

    def wait_for_admission(self, *, estimated_input_tokens, output_tokens, remaining_seconds):
        if self._rate is None or self._ticket is not None:
            return True
        if type(estimated_input_tokens) is not int:
            return False
        self._ticket = self._rate.reserve(estimated_input_tokens + output_tokens, remaining_seconds=remaining_seconds)
        return self._ticket is not None

    def _schema(self, messages):
        view = unpack_view(json.loads(messages[-1]["content"]))
        screening = "dependency" if "screening_contract" in view["initial"] else "provisional"
        return provider_schema(decision_schema(tools=view["initial"]["tools"], screening=screening),
                               allowed_actions=view["decision_protocol"]["allowed_actions"], citation_ids=_ids(view),
                               max_read_requests=view["decision_protocol"].get("max_requests_per_read", 3))

    def estimate_input_tokens(self, messages):
        if self._tokenizer is None:
            raise ValueError("No reviewed input-token estimator")
        # Include the native schema once plus an explicit template margin.
        # Groq's hidden template is not locally certified; any under-estimate stops.
        text = "\n".join(item["role"] + "\n" + item["content"] for item in messages)
        text += json.dumps(self._schema(messages), separators=(",", ":"))
        return len(self._tokenizer.encode(text, disallowed_special=())) + 1024

    def complete(self, messages, *, max_output_tokens, timeout_seconds):
        options = {"max_tokens": max_output_tokens, "timeout": timeout_seconds}
        if self.structured_decisions:
            options["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "shadow_decision", "strict": True, "schema": self._schema(messages)}}
        with tracing_context(enabled=False):
            try:
                response = self.model.bind(**options).invoke(messages)
            except Exception as error:
                self._ticket = None  # Unknown usage leaves the rate reservation charged.
                raise safe_provider_failure(error, self._schema(messages) if self.structured_decisions else None) from None
        usage = token_usage(response)
        if self._rate is not None:
            self._rate.record_usage(self._ticket, usage.get("total_tokens"))
            self._ticket = None
        return DriverReply(content=response.content, usage=usage,
                           finish_reason=(getattr(response, "response_metadata", None) or {}).get("finish_reason"))


def configured_groq_driver(*, structured=False):
    """Trusted caller must approve external project-data transfer before using this factory."""
    from core.config import settings

    if settings.llm_provider.value != "groq":
        raise ValueError("The initial shadow adapter supports only the configured Groq provider")
    from agent.nodes.reasoner import _get_llm

    return GroqShadowDriver(_get_llm(), structured=structured)
