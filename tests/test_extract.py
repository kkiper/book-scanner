import json
from types import SimpleNamespace

from magscan.config import Settings
from magscan.cover import read_cover
from magscan.extract import ClaudeExtractor, _schema

from conftest import make_pdf


def test_schema_is_strict_object():
    s = _schema()
    assert s["type"] == "object" and s["additionalProperties"] is False
    assert set(s["required"]) == set(s["properties"])


def test_claude_request_and_parse(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cover = read_cover(str(make_pdf(tmp_path / "m.pdf")))
    ex = ClaudeExtractor(Settings())
    sent = {}

    def fake_create(**kwargs):
        sent.update(kwargs)
        text = json.dumps({"magazine": "Wired", "year": 2024, "month": 5, "cover_lines": [], "confidence": 0.9})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

    monkeypatch.setattr(ex.client.beta.messages, "create", fake_create)
    info = ex.extract(cover, "m.pdf")
    assert info.magazine == "Wired" and info.month == 5
    assert sent["model"] == "claude-opus-5-5"
    assert sent["fallbacks"] == "default"
    image = sent["messages"][0]["content"][0]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
    assert "m.pdf" in sent["messages"][0]["content"][1]["text"]


def test_missing_key_is_a_clear_error(tmp_path, monkeypatch):
    import pytest
    from magscan.extract import ExtractionError
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    cover = read_cover(str(make_pdf(tmp_path / "m.pdf")))
    with pytest.raises(ExtractionError, match="ANTHROPIC_API_KEY"):
        ClaudeExtractor(Settings()).extract(cover, "m.pdf")
