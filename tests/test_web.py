from fastapi.testclient import TestClient

from conftest import make_pdf
from magscan.web import create_app


def test_review_edit_and_apply_via_ui(env):
    settings, proc, lib, _ = env
    make_pdf(lib / "scan01.pdf")
    client = TestClient(create_app(settings, proc))

    assert client.post("/scan").status_code == 200
    item_id = proc.store.items()[0]["id"]
    proc.analyze(item_id)

    page = client.get("/?status=review")
    assert "National Geographic - 2024-07 - Issue 7.pdf" in page.text
    assert client.get(f"/cover/{item_id}").headers["content-type"] == "image/jpeg"

    form = {f: "" for f in ["volume", "day", "season", "date_text", "topic", "cover_lines"]}
    form.update(magazine="Nat Geo", issue="7", year="2024", month="8", publisher="NG", headline="H", language="en", action="save")
    r = client.post(f"/item/{item_id}", data=form)
    assert "Nat Geo - 2024-08 - Issue 7.pdf" in r.text

    form["action"] = "save_apply"
    client.post(f"/item/{item_id}", data=form)
    proc._queue.join()
    assert (lib / "Nat Geo - 2024-08 - Issue 7.pdf").exists()


def test_settings_preview_and_save(env):
    settings, proc, lib, _ = env
    client = TestClient(create_app(settings, proc))
    p = client.post("/api/preview", json={"filename_template": "{magazine} {year}", "title_template": "{magazine}",
                                          "description_template": "", "author_template": ""}).json()
    assert p["filename"] == "National Geographic 2024.pdf"
    assert "error" in client.post("/api/preview", json={"filename_template": "{bogus}"}).json()

    r = client.post("/settings", data={"filename_template": "{magazine} ({year})", "title_template": "{magazine}",
                                       "description_template": "{headline}", "author_template": "", "categories": "Mags",
                                       "provider": "anthropic", "claude_model": "claude-opus-5-5", "ollama_url": "x",
                                       "ollama_model": "y", "min_confidence_auto_apply": "0.9", "scan_interval_minutes": "0",
                                       "write_sidecar": "on"})
    assert r.status_code == 200
    assert settings.filename_template == "{magazine} ({year})"
    assert settings.write_sidecar and not settings.write_pdf_metadata
    assert settings.settings_file.exists()
