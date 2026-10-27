"""Schema-unit tokenization must not depend on a warm network-downloaded cache."""

from types import SimpleNamespace

import pytest

from agent.shadow_provider import GroqShadowDriver


@pytest.mark.usefixtures("mock_harmony_tokenizer")
def test_schema_driver_never_loads_vocabulary_with_empty_cache(tmp_path, monkeypatch):
    import tiktoken.load

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setattr(tiktoken.load, "read_file", lambda *_: pytest.fail("Unexpected vocabulary download"))
    driver = GroqShadowDriver(SimpleNamespace(max_retries=0, model_name="openai/gpt-oss-120b"), structured=True)
    assert len(driver._tokenizer.encode("synthetic schema text")) > 0
    assert not (tmp_path / "empty-cache").exists()
