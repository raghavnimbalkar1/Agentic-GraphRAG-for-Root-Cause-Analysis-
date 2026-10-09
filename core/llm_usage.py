"""Provider-independent token counts; absent measurements remain unknown."""


def token_usage(response) -> dict:
    usage = getattr(response, "usage_metadata", None) or {}
    if not isinstance(usage, dict):
        usage = {key: getattr(usage, key, None) for key in ("input_tokens", "output_tokens", "total_tokens")}
    result = {key: usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")}
    result = {key: value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
              for key, value in result.items()}
    if result["total_tokens"] is None and result["input_tokens"] is not None and result["output_tokens"] is not None:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result
