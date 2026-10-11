"""Course bundles (app/bundle.py): export → import into another library carries the course content with its ids and
assets, and no learner data; a second import changes nothing; the book is not duplicated; unknown versions are
refused. The examples API lists and imports bundles from the examples folder."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_learning_fixtures import T0, add_capstone, build_course, isolated_engine, learn  # noqa: F401

from app import bundle, db
from app.api import books, courses, examples
from app.api.courses import figure_file
from app.config import settings
from app.learning import engine
from app.pipeline.context import course_dir

LEARNER_TABLES = ("progress", "tasks", "attempts", "review_log", "xp_log", "block_feedback", "jobs")
CONTENT_TABLES = ("pages", "figures", "foundations", "skills", "edges")


def text_pdf(path: Path, title: str = "Bundle Book") -> Path:
    """A 10-page PDF with a text layer (different words on every page); the title only changes the bytes."""
    words = ["vector", "space", "field", "scalar", "basis", "span", "linear", "map", "kernel", "range", "dual", "norm"]
    doc = pymupdf.open()
    for i in range(10):
        text = " ".join(words[(i * 5 + k) % len(words)] for k in range(40))
        doc.new_page(width=300, height=400).insert_textbox(pymupdf.Rect(20, 20, 280, 340), f"Page {i + 1}. {text}", fontsize=8)
    doc.set_metadata({"title": title, "author": "A. Author"})
    doc.save(path)
    doc.close()
    return path


def use_library(root: Path) -> None:
    """Point the app at another data dir + books dir (a second learner's library)."""
    object.__setattr__(settings, "data_dir", root / "data")
    object.__setattr__(settings, "books_dir", root / "books")
    db._initialized_for = None
    db.init_db()


def count(table: str, where: str = "", params: tuple = ()) -> int:
    return db.query_one(f"SELECT COUNT(*) AS n FROM {table} {where}", params)["n"]


@pytest.fixture
def source(tmp_path: Path):
    """A ready course on a real PDF, with a figure, a parsed page, foundations, a flagged and a retired question — and
    a learner's history: a passed lesson, a failed review, XP, a block-feedback note, a foundation answer, a job."""
    pdf_path = text_pdf(tmp_path / "source.pdf")
    book = asyncio.run(books._import(pdf_path, books._sha256_file(pdf_path), "source.pdf"))
    fx = build_course()
    cid = fx.course_id
    db.execute("UPDATE courses SET book_id = ? WHERE id = ?", (book.id, cid))
    s1 = fx.sid("S1")
    db.execute(
        "INSERT INTO figures(id, course_id, page_index, bbox_json, caption, description, image_path) VALUES (?,?,?,?,?,?,?)",
        (f"{cid}-p2-f1", cid, 2, "[0,0,10,10]", "A figure", "Described", "/somewhere/else/p2-f1.png"),
    )
    figure = course_dir(cid) / "figures" / "p2-f1.png"
    figure.parent.mkdir(parents=True)
    figure.write_bytes(b"\x89PNG fake figure")
    (course_dir(cid) / "pages").mkdir()
    (course_dir(cid) / "pages" / "2.png").write_bytes(b"\x89PNG page")
    (course_dir(cid) / "notes.txt").write_text("not an asset")
    db.execute("UPDATE skills SET figure_ids_json = ? WHERE id = ?", (db.dumps([f"{cid}-p2-f1"]), s1))
    db.execute(
        "INSERT INTO pages(course_id, page_index, label, markdown, raw_json) VALUES (?,?,?,?,?)",
        (cid, 2, "3", "Page three.", '{"debug": true}'),
    )
    db.execute(
        "INSERT INTO foundations(course_id, key, title, description, check_question, check_answer, known)"
        " VALUES (?,?,?,?,?,?,1)",
        (cid, "A1", "Fractions", "Adding fractions", "1/2+1/2?", "1"),
    )
    add_capstone(fx, "S2")
    flagged, retired = fx.questions["S1"][0][3], fx.questions["S2"][1][3]
    db.execute("UPDATE questions SET flagged = 1, flag_reason = 'learner: this is wrong' WHERE id = ?", (flagged,))
    db.execute("UPDATE questions SET key = 'old-' || key WHERE id = ?", (retired,))
    db.execute("UPDATE questions SET flag_reason = 'learner said so once' WHERE id = ?", (fx.questions["S1"][1][0],))
    learn(fx, "S1", T0)
    db.execute(
        "INSERT INTO block_feedback(id, skill_id, block_index, note, created_at) VALUES (?,?,?,?,?)",
        (db.new_id(), s1, 0, "This step isn't clear to me", db.now_iso()),
    )
    db.execute(
        "INSERT INTO jobs(course_id, state, stages_json, log_json, updated_at) VALUES (?,?,?,?,?)",
        (cid, "done", "[]", '["cost $1.23"]', db.now_iso()),
    )
    db.kv_set(f"remedial:{s1}", "{}")
    assert count("attempts") > 0 and count("xp_log") > 0 and count("progress", "WHERE card_json IS NOT NULL") == 1
    return fx, book, flagged, retired


def export(fx, tmp_path: Path) -> Path:
    out = tmp_path / "bundle"
    summary = bundle.export_course(fx.course_id, out, description="A demo.", license_name="CC BY-NC 4.0")
    assert summary["missing_figures"] == []
    return out


def test_export_carries_content_only(source, tmp_path: Path) -> None:
    fx, book, flagged, retired = source
    out = export(fx, tmp_path)
    data = json.loads((out / "course.json").read_text())
    assert (data["format"], data["version"]) == (bundle.FORMAT, bundle.VERSION)
    assert set(data) == {"format", "version", "description", "license", "book", "course", *CONTENT_TABLES, "questions"}
    assert data["book"]["sha256"] == books._sha256_file(out / "book.pdf") and len(data["book"]["page_signatures"]) == 10
    assert "book_id" not in data["course"] and data["course"]["id"] == fx.course_id
    question_ids = {q["id"] for q in data["questions"]}
    assert flagged not in question_ids and retired not in question_ids
    assert len(question_ids) == 4 * 2 * 4 - 2 + 1  # 4 skills × 2 KPs × 4, minus flagged and retired, plus the capstone
    assert {q["flag_reason"] for q in data["questions"]} == {""}
    assert [f["known"] for f in data["foundations"]] == [None]
    assert "raw_json" not in data["pages"][0]
    assert data["figures"][0]["image_path"] == f"courses/{fx.course_id}/figures/p2-f1.png"
    assert sorted(p.relative_to(out / "assets").as_posix() for p in (out / "assets").rglob("*") if p.is_file()) == [
        "figures/p2-f1.png",  # pages/ and marks/ (parse intermediates that may show reader marks) are not shipped
    ]
    text = (out / "course.json").read_text()
    for learner_text in ("This step isn't clear", "learner said so once", "learner: this is wrong", "cost $1.23", "remedial"):
        assert learner_text not in text
    assert count("progress", "WHERE status = 'learned'") == 1  # the source is untouched


def test_import_into_a_fresh_library(source, tmp_path: Path) -> None:
    fx, _book, _flagged, _retired = source
    out = export(fx, tmp_path)
    data = json.loads((out / "course.json").read_text())
    use_library(tmp_path / "other")

    result = asyncio.run(bundle.import_bundle(out))
    assert not result.already_imported and result.course_id == fx.course_id
    cid = fx.course_id
    assert {r["id"] for r in db.query("SELECT id FROM skills")} == set(fx.skills.values())
    assert {r["id"] for r in db.query("SELECT id FROM questions")} == {q["id"] for q in data["questions"]}
    assert count("edges") == 3 and count("figures") == 1 and count("pages") == 1 and count("foundations") == 1
    assert db.query_one("SELECT known FROM foundations")["known"] is None
    # pool order kept: questions in the bundle's order (rowid within a KP)
    s1 = fx.sid("S1")
    assert [r["id"] for r in db.query("SELECT id FROM questions WHERE skill_id = ? ORDER BY rowid", (s1,))] == [
        q["id"] for q in data["questions"] if q["skill_id"] == s1
    ]
    # no learner data; fresh progress as after a build: roots available, the rest locked
    for table in ("tasks", "attempts", "review_log", "xp_log", "block_feedback", "jobs"):
        assert count(table) == 0, table
    assert not db.query("SELECT key FROM kv WHERE key LIKE 'remedial:%'")
    statuses = {r["skill_id"]: r["status"] for r in db.query("SELECT * FROM progress")}
    assert statuses == {fx.sid("S1"): "available", fx.sid("S2"): "available", fx.sid("S3"): "locked", fx.sid("S4"): "locked"}
    assert count("progress", "WHERE lesson_attempts > 0 OR card_json IS NOT NULL OR reviews > 0") == 0
    # assets resolve by id; the course is studyable
    figure = db.query_one("SELECT * FROM figures")
    assert figure_file(cid, figure["id"], figure["image_path"]) == course_dir(cid) / "figures" / "p2-f1.png"
    assert not (course_dir(cid) / "pages").exists() and not (course_dir(cid) / "notes.txt").exists()
    task_id = engine.start_lesson(s1, "test", T0 + timedelta(days=1))
    task = engine.begin_practice(task_id, T0 + timedelta(days=1))
    assert task.question is not None and task.question.id in {q["id"] for q in data["questions"]}
    book = db.query_one("SELECT * FROM books")
    assert db.query_one("SELECT book_id FROM courses WHERE id = ?", (cid,))["book_id"] == book["id"]
    assert (settings.books_dir / book["id"] / "book.pdf").is_file()


def test_second_import_is_a_no_op(source, tmp_path: Path) -> None:
    fx, *_ = source
    out = export(fx, tmp_path)
    use_library(tmp_path / "other")
    asyncio.run(bundle.import_bundle(out))
    task_id = engine.start_lesson(fx.sid("S1"), "test", T0)
    before = {t: count(t) for t in (*CONTENT_TABLES, "questions", "books", "courses", "tasks", "progress")}

    again = asyncio.run(bundle.import_bundle(out))
    assert again.already_imported and "already in your library" in again.message
    assert {t: count(t) for t in before} == before
    assert db.query_one("SELECT status FROM tasks WHERE id = ?", (task_id,))["status"] == "active"


@pytest.mark.parametrize("copy", ["identical_file", "same_text"])
def test_book_already_in_the_library_is_reused(source, tmp_path: Path, copy: str) -> None:
    fx, *_ = source
    out = export(fx, tmp_path)
    use_library(tmp_path / "other")
    mine = tmp_path / "mine.pdf"
    if copy == "identical_file":
        mine.write_bytes((out / "book.pdf").read_bytes())
    else:
        text_pdf(mine, title="My own download")  # other bytes, the same pages
    assert (books._sha256_file(mine) == books._sha256_file(out / "book.pdf")) == (copy == "identical_file")
    owned = asyncio.run(books._import(mine, books._sha256_file(mine), "mine.pdf"))

    result = asyncio.run(bundle.import_bundle(out))
    assert result.book_id == owned.id and count("books") == 1
    assert db.query_one("SELECT book_id FROM courses")["book_id"] == owned.id


def test_unknown_version_or_damaged_book_is_refused(source, tmp_path: Path) -> None:
    fx, *_ = source
    out = export(fx, tmp_path)
    use_library(tmp_path / "other")
    data = json.loads((out / "course.json").read_text())
    (out / "course.json").write_text(json.dumps(data | {"version": 99}))
    with pytest.raises(bundle.BundleError, match="format version 99"):
        asyncio.run(bundle.import_bundle(out))
    (out / "course.json").write_text(json.dumps(data | {"book": data["book"] | {"sha256": "0" * 64}}))
    with pytest.raises(bundle.BundleError, match="sha256"):
        asyncio.run(bundle.import_bundle(out))
    assert count("courses") == 0 and count("books") == 0 and not course_dir(fx.course_id).exists()


def test_examples_api(source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, *_ = source
    export_root = tmp_path / "examples"
    bundle.export_course(fx.course_id, export_root / "demo", description="A demo.", license_name="CC BY-NC 4.0",
                         license_url="https://creativecommons.org/licenses/by-nc/4.0/")  # fmt: skip
    (export_root / "not-a-bundle").mkdir()
    monkeypatch.setattr(bundle, "EXAMPLES_DIR", export_root)
    use_library(tmp_path / "other")
    app = FastAPI()
    for router in (books.router, courses.router, examples.router):
        app.include_router(router)
    client = TestClient(app)

    listed = client.get("/api/examples").json()
    assert listed == [{
        "name": "demo", "course_id": fx.course_id, "title": "Chapter 1", "book_title": "Bundle Book", "author": "A. Author",
        "description": "A demo.", "license": "CC BY-NC 4.0", "license_url": "https://creativecommons.org/licenses/by-nc/4.0/",
        "skills": 4, "imported": False,
    }]  # fmt: skip
    resp = client.post("/api/examples/demo/import")
    assert resp.status_code == 200 and resp.json() == {
        "course_id": fx.course_id, "already_imported": False, "message": "“Chapter 1” was added to your library.",
    }
    assert client.get("/api/examples").json()[0]["imported"] is True
    assert client.get(f"/api/courses/{fx.course_id}").json()["status"] == "ready"
    assert client.get(f"/api/courses/{fx.course_id}/figures/p2-f1.png").status_code == 200
    assert client.post("/api/examples/demo/import").json()["already_imported"] is True
    assert client.post("/api/examples/nope/import").status_code == 404
    assert client.post("/api/examples/..%2Fdata/import").status_code == 404


def test_export_strips_the_readers_annotations(tmp_path: Path) -> None:
    """A highlight the learner made in their copy of the book never ships in a bundle's book.pdf; the text is kept."""
    source = text_pdf(tmp_path / "marked.pdf")
    doc = pymupdf.open(source)
    page = doc[3]
    page.add_highlight_annot(pymupdf.Rect(20, 20, 280, 60)).set_info(title="my e-reader")
    doc.saveIncr()
    doc.close()
    assert bundle._clean_pdf_copy(source, tmp_path / "clean.pdf") == 1
    clean = pymupdf.open(tmp_path / "clean.pdf")
    assert sum(len(list(page.annots())) for page in clean) == 0 and "my e-reader" not in (tmp_path / "clean.pdf").read_bytes().decode("latin-1")
    clean.close()
    from app.ingest import pdf

    assert pdf.page_signatures(tmp_path / "clean.pdf") == pdf.page_signatures(source)
    assert bundle._clean_pdf_copy(tmp_path / "clean.pdf", tmp_path / "copy.pdf") == 0
    assert (tmp_path / "copy.pdf").read_bytes() == (tmp_path / "clean.pdf").read_bytes()
