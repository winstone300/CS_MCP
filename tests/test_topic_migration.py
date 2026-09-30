import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from conftest import draft_input, sample_source
from test_presentation import readable_sections
from test_presentation_workflow import approve_v2, review_and_draft

from cs_study_mcp.presentation import profile_snapshot
from cs_study_mcp.render_v2 import render_readable
from cs_study_mcp.service import StudyService


def test_v2_renderer_matches_pre_v3_baseline_exactly():
    """Golden digests obtained from baseline commit 6f0dd45, not the new renderer."""
    f, a = readable_sections()
    rendered = render_readable(
        draft_input(), f, a, {"S1": sample_source()}, profile_snapshot("study_readable_v2"), []
    )
    expected = {
        "markdown": "8cfd52c2a8552436e16aefaa8696e2dda27d3abda2841c21a4992fa3de560995",
        "notion_markdown": "ab26b732f9aff4983d9b53d4ac634cd69fa55f1fde991edcd8ac47141681c58f",
        "html": "7b3044f065e2e7702219cfc2a8dddb2972e559762c96dd313d2dbb28b30bfa3b",
    }
    assert {key: hashlib.sha256(rendered[key].encode()).hexdigest() for key in expected} == expected


def test_v2_to_v3_preserves_approved_publication_and_assets(service):
    actual = StudyService(service.root)
    job_id = actual.create_study("기존 v2", presentation_profile="study_readable_v2")["job_id"]
    actual.save_research(job_id, [sample_source()])
    f, a = readable_sections()
    actual.save_knowledge_section(job_id, f, 0)
    actual.save_knowledge_section(job_id, a, 0)
    draft = review_and_draft(actual, job_id)
    approve_v2(actual, job_id, draft)
    intent = actual.prepare_publication(job_id, draft["version"])
    before = actual.get_study(job_id)
    assets = actual.db.directory / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "existing.txt").write_bytes(b"unchanged fixture asset")
    with actual.db.connect(write=True) as db:
        db.execute("DROP TABLE document_previews")
        db.execute("PRAGMA user_version=2")
    with ThreadPoolExecutor(max_workers=3) as pool:
        instances = list(pool.map(lambda _: StudyService(service.root), range(3)))
    current = instances[0]
    assert current.get_study(job_id) == before
    assert current.prepare_publication(job_id, draft["version"])["action"] == "inspect_before_retry"
    assert current.get_study(job_id)["publication"]["attempt_id"] == intent["attempt_id"]
    backups = list(current.db.directory.glob("backups/schema-v2-*/study.sqlite3"))
    assert backups
    for backup in backups:
        with sqlite3.connect(backup) as db:
            # A parallel initializer may finish before another backup starts.
            assert db.execute("PRAGMA user_version").fetchone()[0] in (2, 3)
            assert (
                db.execute("SELECT content_hash FROM drafts WHERE job_id=?", (job_id,)).fetchone()[
                    0
                ]
                == draft["hash"]
            )
        assert (backup.parent / "assets/existing.txt").read_bytes() == b"unchanged fixture asset"
    count = len(backups)
    StudyService(service.root)
    assert len(list(current.db.directory.glob("backups/schema-v2-*/study.sqlite3"))) == count
    assert (assets / "existing.txt").read_bytes() == b"unchanged fixture asset"
