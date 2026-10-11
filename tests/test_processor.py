import json

import pymupdf

from conftest import make_pdf


def test_full_flow_rename_sidecar_and_undo(env):
    settings, proc, lib, extractor = env
    original = make_pdf(lib / "scans" / "natgeo_0724.pdf")

    assert proc.discover() == 1
    item = proc.store.items()[0]
    proc.analyze(item["id"])
    assert extractor.calls[0][0] == "natgeo_0724.pdf"
    assert "NATIONAL GEOGRAPHIC" in extractor.calls[0][1][0]
    assert proc.store.get(item["id"])["status"] == "review"

    proc.apply(item["id"])
    target = lib / "scans" / "National Geographic - 2024-07 - Issue 7.pdf"
    assert target.exists() and not original.exists()
    sidecar = json.loads((lib / "scans" / "National Geographic - 2024-07 - Issue 7.metadata.json").read_text())
    md = sidecar["metadata"]
    assert md["title"] == "National Geographic - July 2024 (Issue 7)"
    assert md["authors"] == ["NG Partners"]
    assert md["publishedDate"] == "2024-07-01"
    assert md["series"] == {"name": "National Geographic", "number": 7.0}
    assert md["categories"] == ["Magazine", "Science"]
    assert md["description"] == "Octopuses\n\nIn this issue: Amazon; Petra"
    assert md["pageCount"] == 2
    assert sidecar["cover"]["path"] == "National Geographic - 2024-07 - Issue 7.cover.jpg"
    assert (lib / "scans" / sidecar["cover"]["path"]).read_bytes()[:2] == b"\xff\xd8"

    with pymupdf.open(target) as doc:
        assert doc.metadata["title"] == md["title"]
        assert doc.metadata["author"] == "NG Partners"

    proc.undo(item["id"])
    assert original.exists() and not target.exists()
    assert not list((lib / "scans").glob("*.metadata.json"))
    assert proc.store.get(item["id"])["status"] == "review"


def test_edits_override_model_and_collisions_get_suffix(env):
    settings, proc, lib, _ = env
    make_pdf(lib / "a.pdf")
    make_pdf(lib / "b.pdf")
    proc.discover()
    ids = [i["id"] for i in proc.store.items()]
    for i in ids:
        proc.analyze(i)
    proc.store.update(ids[1], edits={"issue": None})
    settings.filename_template = "{magazine}/{year}/{magazine} {month_abbr} {year}"
    for i in ids:
        proc.apply(i)
    folder = lib / "National Geographic" / "2024"
    assert sorted(p.name for p in folder.glob("*.pdf")) == ["National Geographic Jul 2024 (2).pdf", "National Geographic Jul 2024.pdf"]
    # the edited one lost its series number
    numbers = [json.loads(p.read_text())["metadata"]["series"].get("number") for p in folder.glob("*.metadata.json")]
    assert sorted(numbers, key=str) == [7.0, None]


def test_skip_existing_sidecar_and_output_dir(env, tmp_path):
    settings, proc, lib, _ = env
    make_pdf(lib / "done.pdf")
    (lib / "done.metadata.json").write_text("{}")
    make_pdf(lib / "todo.pdf")
    (lib / "notes.txt").write_text("x")
    assert proc.discover() == 1

    settings.output_dir = str(tmp_path / "out")
    item = proc.store.items()[0]
    proc.analyze(item["id"])
    proc.apply(item["id"])
    assert (tmp_path / "out" / "National Geographic - 2024-07 - Issue 7.pdf").exists()


def test_auto_apply_respects_confidence(env):
    settings, proc, lib, extractor = env
    settings.auto_apply = True
    extractor.info = extractor.info.model_copy(update={"confidence": 0.5})
    make_pdf(lib / "x.pdf")
    proc.discover()
    item = proc.store.items()[0]
    proc.analyze(item["id"])
    assert proc.store.get(item["id"])["status"] == "review"
    extractor.info = extractor.info.model_copy(update={"confidence": 0.9})
    proc.analyze(item["id"])
    assert proc.store.get(item["id"])["status"] == "applied"


def test_broken_file_is_marked_error(env):
    settings, proc, lib, _ = env
    lib.mkdir(parents=True)
    (lib / "broken.pdf").write_bytes(b"not a pdf")
    proc.discover()
    item = proc.store.items()[0]
    proc.analyze(item["id"])
    assert proc.store.get(item["id"])["status"] == "error"
