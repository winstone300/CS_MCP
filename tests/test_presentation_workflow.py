"""Synthetic workflow tests, not live Notion/content verification."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest
from conftest import PAGE_ID, PARENT_ID, draft_input, sample_sections, sample_source

from cs_study_mcp.models import (
    ComparisonTable,
    CrossReview,
    Placement,
    PublicationInput,
    ReviewInput,
    TableRow,
    Visual,
)
from cs_study_mcp.render import digest
from cs_study_mcp.runner import check_publication_approval
from cs_study_mcp.service import StudyService, WorkflowError
from cs_study_mcp.storage import SCHEMA, dumps


def readable_sections():
    f, a = sample_sections()
    for section, fields in (
        (f, ("terms", "principles", "examples", "misconceptions")),
        (a, ("concepts", "tradeoffs", "cases")),
    ):
        for field in fields:
            for i, item in enumerate(getattr(section, field)):
                item.id = f"{section.kind}-{field}-{i}"
                if hasattr(item, "key_point"):
                    item.key_point = "모의 핵심 문장"
    f.misconceptions[0].related_item_id = f.principles[0].id
    f.comparisons = [
        ComparisonTable(
            id="comparison",
            title="모의 비교",
            columns=["조건", "결과"],
            rows=[TableRow(cells=["A", "B"])],
            claim_ids=["F1"],
            placement=Placement(section="foundation", after_id=f.principles[0].id),
        )
    ]
    return f, a


@pytest.fixture
def v2(service):
    real = StudyService(service.root)
    job = real.create_study("합성 시각 문서 fixture", presentation_profile="study_readable_v2")
    real.save_research(job["job_id"], [sample_source()])
    f, a = readable_sections()
    real.save_knowledge_section(job["job_id"], f, 0)
    real.save_knowledge_section(job["job_id"], a, 0)
    return real, job["job_id"]


def review_and_draft(service, job_id):
    report = service.validate_presentation(job_id)
    assert report["valid"], report
    for role in ("foundation", "advanced"):
        service.record_cross_review(
            job_id,
            CrossReview(
                reviewer=role,
                foundation_version=report["versions"]["foundation"],
                advanced_version=report["versions"]["advanced"],
                research_revision=service.get_study(job_id)["research_revision"],
                presentation_hash=report["presentation_hash"],
                summary="실제 검증이 아닌 테스트 fixture",
            ),
        )
    return service.save_draft(
        job_id,
        draft_input(
            foundation_version=report["versions"]["foundation"],
            advanced_version=report["versions"]["advanced"],
        ),
    )


def approve_v2(service, job_id, draft):
    return service.record_review(
        job_id,
        ReviewInput(
            draft_version=draft["version"],
            draft_hash=draft["hash"],
            bundle_hash=draft["bundle_hash"],
            parent_page_id=PARENT_ID,
            decision="approve",
            user_message="합성 테스트 승인",
        ),
    )


def test_new_default_profile_and_snapshot_are_frozen(v2, monkeypatch):
    service, job_id = v2
    before = service.get_document_blueprint(job_id, "foundation")
    assert before["profile"]["name"] == "study_readable_v2"
    monkeypatch.setattr("cs_study_mcp.service.profile_snapshot", lambda _: {"name": "future"})
    assert service.get_document_blueprint(job_id, "foundation") == before


def test_warnings_allow_draft_and_bundle_is_explicit(v2):
    service, job_id = v2
    assert service.validate_presentation(job_id)["warnings"]
    draft = review_and_draft(service, job_id)
    assert draft["bundle_hash"] != draft["hash"]
    assert Path(draft["preview_path"]).exists()
    assert draft["markdown"].index("## 핵심 요약") < draft["markdown"].index("## 학습 목표")
    with pytest.raises(WorkflowError, match="stale_approval"):
        service.record_review(
            job_id,
            ReviewInput(
                draft_version=draft["version"],
                draft_hash=draft["hash"],
                parent_page_id=PARENT_ID,
                decision="approve",
                user_message="합성 테스트 승인",
            ),
        )
    approve_v2(service, job_id, draft)
    study = service.get_study(job_id)
    check_publication_approval(study)
    study["reviews"][-1]["bundle_hash"] = "wrong"
    with pytest.raises(WorkflowError, match="approval_required"):
        check_publication_approval(study)


def test_readback_requires_structure_and_actual_visual_confirmation(v2):
    service, job_id = v2
    draft = review_and_draft(service, job_id)
    approve_v2(service, job_id, draft)
    intent = service.prepare_publication(job_id, draft["version"])
    base = dict(
        attempt_id=intent["attempt_id"],
        outcome="verified",
        page_id=PAGE_ID,
        observed_title=intent["title"],
        observed_parent_page_id=PARENT_ID,
    )
    result = service.record_publication(
        job_id, PublicationInput(**base, observed_markdown=intent["markdown"])
    )
    assert result["status"] == "needs_attention"
    changed_table = intent["markdown"].replace("<td>B</td>", "<td>CHANGED</td>")
    assert changed_table != intent["markdown"]
    result = service.record_publication(
        job_id,
        PublicationInput(
            **base,
            observed_markdown=changed_table,
            observed_visual_check=True,
        ),
    )
    assert result["status"] == "needs_attention"
    result = service.record_publication(
        job_id,
        PublicationInput(**base, observed_markdown=intent["markdown"], observed_visual_check=True),
    )
    assert result["status"] == "completed"


def test_stale_visual_review_and_incomplete_initial_drafts(v2):
    service, job_id = v2
    with pytest.raises(WorkflowError, match="stale_visual_review"):
        service.record_cross_review(
            job_id,
            CrossReview(
                reviewer="foundation",
                foundation_version=1,
                advanced_version=1,
                research_revision=1,
                summary="해시가 없는 검토",
            ),
        )
    f, _ = readable_sections()
    f.visuals = [
        Visual(
            id="diagram",
            kind="structure",
            title="모의 구조",
            caption="합성 시험",
            alt_text="A에서 B로",
            diagram_spec="flowchart LR\nA --> B",
            claim_ids=["F1"],
            placement=Placement(section="foundation"),
        )
    ]
    result = service.save_knowledge_section(job_id, f, 1)
    assert result["version"] == 2
    assert any(
        e["code"] == "missing_asset" for e in service.validate_presentation(job_id)["errors"]
    )


def test_asset_tampering_blocks_review_and_publication(v2, monkeypatch):
    from cs_study_mcp.assets import store_asset

    service, job_id = v2
    original = service.root / "test.svg"
    original.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="30"><text x="1" y="20">test</text></svg>'
    )
    metadata = store_asset(service.root, original, generated=True)
    monkeypatch.setattr(
        "cs_study_mcp.assets.render_diagram",
        lambda *args: {**metadata, "spec_hash": "fixed", "renderer_fingerprint": "mock-only"},
    )
    f, a = readable_sections()
    f.visuals = [
        Visual(
            id="diagram",
            kind="structure",
            title="모의 구조",
            caption="합성 시험",
            alt_text="A에서 B로",
            diagram_spec="flowchart LR\nA --> B",
            claim_ids=["F1"],
            placement=Placement(section="foundation"),
        )
    ]
    service.save_knowledge_section(job_id, f, 1)
    a.foundation_version = 2
    service.save_knowledge_section(job_id, a, 1)
    service.prepare_visual_assets(job_id, {"foundation": 2, "advanced": 2})
    draft = review_and_draft(service, job_id)
    approve_v2(service, job_id, draft)
    path = Path(metadata["path"])
    if not path.is_absolute():
        path = service.root / path
    path.write_bytes(b"tampered")
    with pytest.raises(WorkflowError, match="presentation_invalid"):
        service.prepare_publication(job_id, draft["version"])


def test_saved_draft_survives_export_failure(v2, monkeypatch):
    service, job_id = v2

    def broken(*args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(service, "_export", broken)
    draft = review_and_draft(service, job_id)
    assert draft["export_error"] == "synthetic disk failure"
    assert service.get_study(job_id)["draft"]["version"] == draft["version"]


def test_legacy_defaults_do_not_invalidate_existing_content(populated):
    service, job_id = populated
    f, _ = sample_sections()
    old = f.model_dump(mode="json")
    for field in ("terms", "principles", "examples", "misconceptions"):
        for item in old[field]:
            for key in ("id", "key_point", "related_item_id"):
                item.pop(key, None)
    old.pop("visuals")
    old.pop("comparisons")
    raw = dumps(old)
    with service.db.connect(write=True) as db:
        db.execute(
            "UPDATE sections SET content=?,content_hash=? WHERE job_id=? AND kind='foundation'",
            (raw, digest(raw), job_id),
        )
    assert service.save_knowledge_section(job_id, f, 1)["unchanged"]
    saved = service.get_knowledge_section(job_id, "foundation")
    assert saved["hash"] == digest(raw)
    assert saved["version"] == 1


def test_v1_migration_preserves_records_and_makes_backup(tmp_path):
    directory = tmp_path / ".cs-study"
    directory.mkdir()
    with sqlite3.connect(directory / "study.sqlite3") as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO jobs(id,topic,level,question_count,status,rules_text,rules_hash,created_at,updated_at,current_draft_version) VALUES('old','topic','level',6,'approved','rules','hash','date','date',1)"
        )
        db.execute(
            "INSERT INTO drafts VALUES('old',1,'{}','exact markdown','original hash',1,1,1,'date')"
        )
        db.execute(
            "INSERT INTO reviews(job_id,draft_version,draft_hash,parent_page_id,decision,user_message,feedback,created_at) VALUES('old',1,'original hash','parent','approve','actual user text','','date')"
        )
    service = StudyService(tmp_path)
    study = service.get_study("old")
    assert study["presentation_profile"]["name"] == "legacy_v1"
    assert study["draft"]["markdown"] == "exact markdown"
    assert study["draft"]["content_hash"] == study["reviews"][0]["draft_hash"] == "original hash"
    backups = list((directory / "backups").glob("schema-v1-*/study.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    StudyService(tmp_path)
    assert len(list((directory / "backups").glob("schema-v1-*"))) == 1


def test_approval_detects_changed_stored_bundle(v2):
    service, job_id = v2
    draft = review_and_draft(service, job_id)
    with service.db.connect(write=True) as db:
        db.execute(
            "UPDATE drafts SET notion_markdown=notion_markdown || 'changed' WHERE job_id=?",
            (job_id,),
        )
    with pytest.raises(WorkflowError, match="bundle_changed"):
        approve_v2(service, job_id, draft)


def test_upload_receipt_requires_active_attempt_and_approved_asset(v2):
    service, job_id = v2
    with pytest.raises(WorkflowError, match="unknown_attempt"):
        service.record_publication_asset(
            job_id, "unknown", "f" * 64, "provider-file-1", "https://example.org/image.png"
        )
    draft = review_and_draft(service, job_id)
    approve_v2(service, job_id, draft)
    intent = service.prepare_publication(job_id, draft["version"])
    with pytest.raises(WorkflowError, match="unknown_asset"):
        service.record_publication_asset(
            job_id,
            intent["attempt_id"],
            "f" * 64,
            "provider-file-1",
            "https://example.org/image.png",
        )


def test_parallel_fresh_database_initialization(tmp_path):
    with ThreadPoolExecutor(max_workers=5) as pool:
        instances = list(pool.map(lambda _: StudyService(tmp_path), range(10)))
    with instances[0].db.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_request_changes_can_revoke_approval_with_broken_bundle(v2):
    service, job_id = v2
    draft = review_and_draft(service, job_id)
    approve_v2(service, job_id, draft)
    with service.db.connect(write=True) as db:
        db.execute("UPDATE drafts SET preview_html='damaged' WHERE job_id=?", (job_id,))
    review = ReviewInput(
        draft_version=draft["version"],
        draft_hash=draft["hash"],
        bundle_hash=draft["bundle_hash"],
        parent_page_id=PARENT_ID,
        decision="request_changes",
        user_message="합성 테스트: 수정 요청",
    )
    assert service.record_review(job_id, review)["status"] == "changes_requested"
    with pytest.raises(WorkflowError):
        service.prepare_publication(job_id, draft["version"])


def test_read_payload_does_not_grant_another_creation(v2):
    service, job_id = v2
    draft = review_and_draft(service, job_id)
    approve_v2(service, job_id, draft)
    intent = service.prepare_publication(job_id, draft["version"])
    before = service.get_study(job_id)
    payload = service.get_publication_payload(job_id, intent["attempt_id"])
    assert payload["markdown"] == intent["markdown"]
    assert "action" not in payload
    assert service.get_study(job_id) == before
    assert service.prepare_publication(job_id, draft["version"])["action"] == "inspect_before_retry"


def test_late_asset_preparation_preserves_approved_draft(v2, monkeypatch):
    from cs_study_mcp.assets import store_asset

    service, job_id = v2
    original = service.root / "race.svg"
    original.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><text x="1" y="10">x</text></svg>'
    )
    metadata = store_asset(service.root, original, generated=True)
    f, a = readable_sections()
    f.visuals = [
        Visual(
            id="diagram",
            kind="structure",
            title="mock",
            caption="mock",
            alt_text="mock",
            diagram_spec="flowchart LR\nA-->B",
            claim_ids=["F1"],
            placement=Placement(section="foundation"),
        )
    ]
    service.save_knowledge_section(job_id, f, 1)
    a.foundation_version = 2
    service.save_knowledge_section(job_id, a, 1)
    entered, release, lock = Event(), Event(), Lock()
    counter = 0

    def render(*args):
        nonlocal counter
        with lock:
            counter += 1
            number = counter
        if number == 1:
            entered.set()
            assert release.wait(20)
        return {**metadata, "spec_hash": "fixed", "renderer_fingerprint": "mock"}

    monkeypatch.setattr("cs_study_mcp.assets.render_diagram", render)
    with ThreadPoolExecutor(max_workers=1) as pool:
        slow = pool.submit(service.prepare_visual_assets, job_id, {"foundation": 2, "advanced": 2})
        try:
            assert entered.wait(5)
            service.prepare_visual_assets(job_id, {"foundation": 2, "advanced": 2})
            draft = review_and_draft(service, job_id)
            approve_v2(service, job_id, draft)
        finally:
            release.set()
        assert slow.result()["valid"]
    current = service.get_study(job_id)
    assert current["status"] == "approved"
    assert current["draft"]["bundle_hash"] == draft["bundle_hash"]
