"""Policy, recovery and approval gates using synthetic receipts/assets only."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from conftest import PAGE_ID, PARENT_ID, sample_source
from pydantic import ValidationError
from test_assets import PNG
from test_image_citations import capture, screenshot
from test_presentation_workflow import approve_v2
from test_presentation_workflow import readable_sections as old_sections
from test_topic_workflow import make_plan, promote, review
from visual_helpers import add_text_assessments, expression_review, review_refs

from cs_study_mcp.models import (
    CrossReview,
    Finding,
    Placement,
    PublicationInput,
    Visual,
    VisualAcquisitionInput,
)
from cs_study_mcp.presentation import presentation_issues, profile_snapshot
from cs_study_mcp.server import create_server
from cs_study_mcp.service import StudyService, WorkflowError
from cs_study_mcp.storage import dumps


@pytest.fixture
def visual_job(service):
    actual = StudyService(service.root)
    job_id = actual.create_study("합성 시각 자료 정책 시험")["job_id"]
    actual.save_research(job_id, [sample_source()])
    f, a = add_text_assessments(*old_sections())
    return actual, job_id, f, a


def save_pair(service, job_id, f, a, expected=0):
    saved = service.save_knowledge_section(job_id, f, expected)
    a.foundation_version = saved["version"]
    service.save_knowledge_section(job_id, a, expected)


def image_request(f):
    item = f.visual_assessments[0]
    item.preferred_kind = "image"
    item.acquisition_plan = "확인된 실습 환경에서 결과 창을 직접 캡처한다."
    item.status = "pending"
    item.selected_kind = None
    item.explanation_item_id = None
    return item


def receipt(f, **changes):
    return VisualAcquisitionInput.model_validate(
        {
            "result_id": "result-1",
            "section": "foundation",
            "assessment_id": f.visual_assessments[0].id,
            "expected_section_version": 1,
            "procedure": "실습 실행 환경과 캡처 지원을 확인했다.",
            "environment": "합성 테스트 환경",
            "checked_at": "2026-10-06T12:00:00+09:00",
            "outcome": "unavailable",
            "constraint": "모의 실행 환경에 해당 실습 도구가 없다.",
            **changes,
        }
    )


def fallback(service, job_id, f, a):
    assessment = image_request(f)
    save_pair(service, job_id, f, a)
    service.record_visual_acquisition(job_id, receipt(f))
    assessment.status = "resolved"
    assessment.selected_kind = "text"
    assessment.result_ids = ["result-1"]
    assessment.explanation_item_id = assessment.item_id
    assessment.change_reason = "확인한 환경 제약으로 실행 화면 대신 본문의 단계별 설명을 사용한다."
    assessment.reader_note = (
        "이 환경에서는 실습 화면을 확보하지 못해 동작을 단계별 텍스트로 설명합니다."
    )
    save_pair(service, job_id, f, a, 1)
    return service.prepare_document_preview(
        job_id, make_plan(f, a, foundation_version=2, advanced_version=2), 0
    )


def codes(report):
    return {item["code"] for item in report["errors"]}


def test_policy_freezes_on_creation_and_only_new_v3_has_it(visual_job):
    service, job_id, _, _ = visual_job
    assert (
        service.get_study(job_id)["presentation_profile"]["visual_assessment_policy_version"] == 1
    )
    for name in ("legacy_v1", "study_readable_v2"):
        assert "visual_assessment_policy_version" not in profile_snapshot(name)
    assert service.get_document_blueprint(job_id, "foundation")["profile"]["guidance"][
        "visual_assessments"
    ]


def test_unfinished_draft_saves_but_final_candidate_requires_all_assessments(visual_job):
    service, job_id, f, a = visual_job
    f.visual_assessments = []
    a.visual_assessments[0].status = "pending"
    save_pair(service, job_id, f, a)
    report = service.validate_presentation(job_id)
    assert {"missing_visual_assessment", "unresolved_visual_assessment"} <= codes(report)
    with pytest.raises(WorkflowError, match="validation_failed"):
        service.prepare_document_preview(job_id, make_plan(f, a), 0)


@pytest.mark.parametrize(
    "change,code",
    [
        ("duplicate_id", "duplicate_visual_assessment"),
        ("duplicate_target", "duplicate_assessment_target"),
        ("target", "unknown_assessment_target"),
        ("source", "unknown_assessment_source"),
        ("explanation", "missing_visual_explanation"),
        ("claim", "unknown_assessment_claim"),
        ("visual", "unknown_assessment_visual"),
        ("placement", "assessment_visual_mismatch"),
        ("fallback", "missing_visual_fallback_note"),
    ],
)
def test_structural_assessment_errors(visual_job, change, code):
    _, _, f, a = visual_job
    item = f.visual_assessments[0]
    if change.startswith("duplicate"):
        other = item.model_copy(deep=True)
        if change == "duplicate_target":
            other.id = "different-assessment"
        f.visual_assessments.append(other)
    elif change == "target":
        item.item_id = "unknown"
    elif change == "source":
        item.source_ids = ["unknown"]
    elif change == "explanation":
        item.explanation_item_id = "unknown"
    elif change == "claim":
        item.claim_ids = ["unknown"]
    elif change in ("visual", "placement"):
        item.preferred_kind = item.selected_kind = "diagram"
        item.visual_ids = ["flow"]
        if change == "placement":
            f.visuals = [
                Visual(
                    id="flow",
                    kind="flow",
                    title="모의 흐름",
                    caption="모의 캡션",
                    alt_text="모의 흐름",
                    diagram_spec="flowchart LR\nA-->B",
                    claim_ids=["F1"],
                    placement=Placement(section="foundation", after_id=f.principles[0].id),
                )
            ]
    else:
        item.preferred_kind = "image"
        item.acquisition_plan = "모의 캡처 계획"
        item.result_ids = ["failed"]
        item.change_reason = "환경 제약"
    assert code in codes(
        presentation_issues(f, a, {"S1": sample_source()}, profile_snapshot("study_topic_v3"))
    )


def test_text_and_diagram_can_complete_without_screenshot(visual_job, monkeypatch):
    from cs_study_mcp.assets import store_asset

    service, job_id, f, a = visual_job
    item = f.visual_assessments[0]
    item.preferred_kind = item.selected_kind = "diagram"
    item.visual_ids = ["example-flow"]
    f.visuals = [
        Visual(
            id="example-flow",
            kind="flow",
            title="모의 흐름",
            caption="모의 캡션",
            alt_text="예시 동작 순서",
            claim_ids=["F1"],
            diagram_spec="flowchart LR\nA-->B",
            placement=Placement(section="foundation", after_id=item.item_id),
        )
    ]
    path = service.root / "diagram-fixture.png"
    path.write_bytes(PNG)
    asset = store_asset(service.root, path, generated=True)
    monkeypatch.setattr(
        "cs_study_mcp.assets.render_diagram",
        lambda *args: {**asset, "spec_hash": "fixture", "renderer_fingerprint": "fixture"},
    )
    save_pair(service, job_id, f, a)
    assert service.prepare_visual_assets(job_id, {"foundation": 1, "advanced": 1})["valid"]
    preview = service.prepare_document_preview(job_id, make_plan(f, a), 0)
    assert preview["current"] and "```mermaid" in preview["markdown"]
    assert service.get_visual_acquisitions(job_id)["results"] == []
    review(service, job_id, preview)
    promote(service, job_id, preview)


def test_acquisition_restart_idempotence_and_author_reflection(visual_job):
    service, job_id, f, a = visual_job
    image_request(f)
    save_pair(service, job_id, f, a)
    before = service.get_knowledge_section(job_id, "foundation")
    original = receipt(f)
    result = service.record_visual_acquisition(job_id, original)
    assert not result["idempotent"]
    restarted = StudyService(service.root)
    records = restarted.get_visual_acquisitions(job_id)["results"]
    assert records[0]["constraint"] == original.constraint and records[0]["intact"]
    assert restarted.get_knowledge_section(job_id, "foundation") == before
    assert restarted.record_visual_acquisition(job_id, original)["idempotent"]
    assert "unreflected_acquisition_result" in codes(restarted.validate_presentation(job_id))
    with pytest.raises(WorkflowError, match="acquisition_conflict"):
        restarted.record_visual_acquisition(job_id, receipt(f, procedure="다른 수행 내용"))
    # Duplicate submissions remain idempotent after the author advances its section.
    item = f.visual_assessments[0]
    item.result_ids = [original.result_id]
    item.status = "resolved"
    item.selected_kind = "text"
    item.explanation_item_id = item.item_id
    item.change_reason = "실습 도구 미지원"
    item.reader_note = "동작을 본문으로 설명합니다."
    save_pair(restarted, job_id, f, a, 1)
    assert restarted.record_visual_acquisition(job_id, original)["idempotent"]


@pytest.mark.parametrize(
    "change,code",
    [
        ("version", "version_conflict"),
        ("assessment", "unknown_assessment"),
        ("job", "version_conflict"),
        ("asset", "asset_integrity"),
    ],
)
def test_acquisition_rejects_wrong_context(visual_job, change, code):
    service, job_id, f, a = visual_job
    image_request(f)
    save_pair(service, job_id, f, a)
    args = {}
    if change == "version":
        args["expected_section_version"] = 2
    elif change == "assessment":
        args["assessment_id"] = "unknown"
    elif change == "job":
        job_id = service.create_study("다른 작업")["job_id"]
    else:
        args.update(outcome="acquired", constraint=None, asset_hash="a" * 64, screenshot=capture())
    with pytest.raises(WorkflowError, match=code):
        service.record_visual_acquisition(job_id, receipt(f, **args))


@pytest.mark.parametrize(
    "data",
    [
        {"constraint": None},
        {"asset_hash": "a" * 64},
        {"outcome": "acquired", "constraint": None},
        {"checked_at": "2026-10-06T12:00:00"},
    ],
)
def test_acquisition_schema_requires_actual_result_details(visual_job, data):
    with pytest.raises(ValidationError):
        receipt(visual_job[2], **data)


@pytest.mark.parametrize("direct", [True, False])
def test_public_and_direct_images_use_recorded_provenance(visual_job, direct):
    service, job_id, f, a = visual_job
    assessment = image_request(f)
    save_pair(service, job_id, f, a)
    path = service.root / "screen-fixture.png"
    path.write_bytes(PNG)
    asset = service.register_visual_asset(job_id, str(path))
    assert service.get_visual_acquisitions(job_id)["registered_assets"][0]["hash"] == asset["hash"]
    provenance = (
        capture()
        if direct
        else capture(
            capture_method="public_image",
            image_url="https://example.org/fixture.png",
            capture_target=None,
            captured_at=None,
            capture_environment=None,
        )
    )
    acquired = receipt(
        f, outcome="acquired", constraint=None, asset_hash=asset["hash"], screenshot=provenance
    )
    service.record_visual_acquisition(job_id, acquired)
    restarted = StudyService(service.root)
    assessment.result_ids = ["result-1"]
    assessment.status = "resolved"
    assessment.selected_kind = "image"
    assessment.visual_ids = ["screen"]
    f.visuals = [screenshot(provenance, asset["hash"], assessment.item_id)]
    save_pair(restarted, job_id, f, a, 1)
    assert restarted.prepare_visual_assets(job_id, {"foundation": 2, "advanced": 2})["valid"]
    preview = restarted.prepare_document_preview(
        job_id, make_plan(f, a, foundation_version=2, advanced_version=2), 0
    )
    assert preview["visual_assessments"][0]["acquisition_results"][0]["hash"]
    assert "![실습 결과 화면]" in preview["markdown"]
    f.visuals[0].screenshot.usage_note = "다른 이용 조건"
    save_pair(restarted, job_id, f, a, 2)
    assert "acquisition_visual_mismatch" in codes(restarted.validate_presentation(job_id))


def test_fallback_requires_semantic_review_and_survives_publication_readback(visual_job):
    service, job_id, f, a = visual_job
    preview = fallback(service, job_id, f, a)
    note = f.visual_assessments[0].reader_note
    assert preview["markdown"].count(note) == preview["notion_markdown"].count(note) == 1
    assert "모의 실행 환경에 해당 실습 도구가 없다." not in preview["markdown"]
    assert preview["visual_assessments"][0]["acquisition_results"][0]["constraint"]
    blocked = CrossReview(
        reviewer="foundation",
        foundation_version=2,
        advanced_version=2,
        research_revision=1,
        presentation_hash=preview["presentation_hash"],
        reviewed_visual_assessments=review_refs(preview),
        korean_expression_review=expression_review(preview, "foundation"),
        summary="모의 대체 설명 근거 부족",
        findings=[
            Finding(severity="blocking", location="foundation.example", comment="핵심 근거 부족")
        ],
    )
    service.record_cross_review(job_id, blocked)
    with pytest.raises(WorkflowError, match="cross_review_required"):
        promote(service, job_id, preview)
    review(service, job_id, preview)
    draft = promote(service, job_id, preview)
    approve_v2(service, job_id, draft)
    intent = service.prepare_publication(job_id, draft["version"])
    service.record_publication(
        job_id,
        PublicationInput(attempt_id=intent["attempt_id"], outcome="page_created", page_id=PAGE_ID),
    )
    base = dict(
        attempt_id=intent["attempt_id"],
        outcome="verified",
        page_id=PAGE_ID,
        observed_title=intent["title"],
        observed_parent_page_id=PARENT_ID,
    )
    assert (
        service.record_publication(
            job_id, PublicationInput(**base, observed_markdown=intent["markdown"].replace(note, ""))
        )["status"]
        == "needs_attention"
    )
    assert (
        service.record_publication(
            job_id, PublicationInput(**base, observed_markdown=intent["markdown"])
        )["status"]
        == "completed"
    )
    with pytest.raises(WorkflowError, match="publication_locked"):
        service.record_visual_acquisition(
            job_id, receipt(f, result_id="late", expected_section_version=2)
        )


@pytest.mark.parametrize("scope", ["missing", "duplicate", "unknown"])
def test_review_requires_exact_candidate_assessment_coverage(visual_job, scope):
    service, job_id, f, a = visual_job
    save_pair(service, job_id, f, a)
    preview = service.prepare_document_preview(job_id, make_plan(f, a), 0)
    refs = review_refs(preview)
    if scope == "missing":
        refs.pop()
    elif scope == "duplicate":
        refs.append(refs[0])
    else:
        refs[0] = {"section": "foundation", "assessment_id": "unknown"}
    with pytest.raises(WorkflowError, match="visual_review_coverage"):
        service.record_cross_review(
            job_id,
            CrossReview(
                reviewer="foundation",
                foundation_version=1,
                advanced_version=1,
                research_revision=1,
                presentation_hash=preview["presentation_hash"],
                summary="모의 검토",
                reviewed_visual_assessments=refs,
            ),
        )


def test_assessment_only_changes_invalidate_review_and_approval(visual_job):
    service, job_id, f, a = visual_job
    save_pair(service, job_id, f, a)
    preview = service.prepare_document_preview(job_id, make_plan(f, a), 0)
    review(service, job_id, preview)
    draft = promote(service, job_id, preview)
    approve_v2(service, job_id, draft)
    f.visual_assessments[0].rationale = "본문은 동일하지만 선택 근거를 보완한다."
    save_pair(service, job_id, f, a, 1)
    updated = service.prepare_document_preview(
        job_id, make_plan(f, a, foundation_version=2, advanced_version=2), 1
    )
    assert updated["markdown"] == preview["markdown"]
    assert updated["presentation_hash"] != preview["presentation_hash"]
    assert (
        service.get_document_preview(job_id, 1)["visual_assessments"]
        == preview["visual_assessments"]
    )
    assert not service.get_document_preview(job_id, 1)["current"]
    with pytest.raises(WorkflowError):
        service.prepare_publication(job_id, draft["version"])
    with pytest.raises(WorkflowError, match="cross_review_required"):
        promote(service, job_id, updated)


def test_new_receipt_alone_invalidates_candidate_until_reflected(visual_job):
    service, job_id, f, a = visual_job
    preview = fallback(service, job_id, f, a)
    review(service, job_id, preview)
    draft = promote(service, job_id, preview)
    approve_v2(service, job_id, draft)
    before = service.get_knowledge_section(job_id, "foundation")
    service.record_visual_acquisition(
        job_id,
        receipt(
            f, result_id="result-2", expected_section_version=2, constraint="새로 확인한 환경 제약"
        ),
    )
    assert service.get_knowledge_section(job_id, "foundation") == before
    assert {"stale_preview", "unreflected_acquisition_result"} <= codes(
        service.validate_presentation(job_id)
    )
    with pytest.raises(WorkflowError):
        service.prepare_publication(job_id, draft["version"])
    f.visual_assessments[0].result_ids.append("result-2")
    save_pair(service, job_id, f, a, 2)
    newer = service.prepare_document_preview(
        job_id, make_plan(f, a, foundation_version=3, advanced_version=3), 1
    )
    assert newer["presentation_hash"] != preview["presentation_hash"]


@pytest.mark.parametrize("change", ["body", "request", "delete", "result_job", "tamper"])
def test_stale_or_foreign_or_modified_results_block_final_candidate(visual_job, change):
    service, job_id, f, a = visual_job
    fallback(service, job_id, f, a)
    if change == "body":
        f.examples[0].body = "변경된 본문"
    elif change == "request":
        f.visual_assessments[0].learning_goal = "새로운 관찰 목표"
    elif change == "delete":
        f.examples = []
    elif change == "result_job":
        f.visual_assessments[0].result_ids = ["another-job-result"]
    else:
        with service.db.connect(write=True) as db:
            record = json.loads(
                db.execute(
                    "SELECT content FROM visual_acquisitions WHERE job_id=?", (job_id,)
                ).fetchone()[0]
            )
            record["constraint"] = "조작된 제약"
            db.execute(
                "UPDATE visual_acquisitions SET content=? WHERE job_id=?", (dumps(record), job_id)
            )
    if change != "tamper":
        save_pair(service, job_id, f, a, 2)
    expected = {
        "body": "stale_acquisition_result",
        "request": "stale_acquisition_result",
        "delete": "unknown_assessment_target",
        "result_job": "unknown_acquisition_result",
        "tamper": "acquisition_integrity",
    }[change]
    assert expected in codes(service.validate_presentation(job_id))


def test_acquisition_during_render_blocks_candidate_race(visual_job, monkeypatch):
    import cs_study_mcp.render_v3 as renderer

    service, job_id, f, a = visual_job
    save_pair(service, job_id, f, a)
    original = renderer.render_topic
    started, resume = Event(), Event()

    def slow(*args):
        started.set()
        assert resume.wait(10)
        return original(*args)

    monkeypatch.setattr(renderer, "render_topic", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.prepare_document_preview, job_id, make_plan(f, a), 0)
        try:
            assert started.wait(10)
            service.record_visual_acquisition(job_id, receipt(f))
        finally:
            resume.set()
        with pytest.raises(WorkflowError, match="version_conflict"):
            pending.result(timeout=10)


@pytest.mark.parametrize("profile_name", ["legacy_v1", "study_readable_v2", "study_topic_v3"])
def test_old_profiles_preserve_serialization_and_reject_new_policy_writes(
    service, monkeypatch, profile_name
):
    old = profile_snapshot(profile_name)
    old.pop("visual_assessment_policy_version", None)
    old.pop("korean_expression_review_version", None)
    old["guidance"].pop("visual_assessments", None)
    with monkeypatch.context() as context:
        context.setattr("cs_study_mcp.service.profile_snapshot", lambda _: old)
        actual = StudyService(service.root)
        job_id = actual.create_study("기존 작업", presentation_profile=profile_name)["job_id"]
    actual.save_research(job_id, [sample_source()])
    f, a = old_sections()
    if profile_name == "legacy_v1":
        f.comparisons = []
    assert "visual_assessments" not in f.model_dump(mode="json")
    save_pair(actual, job_id, f, a)
    before = actual.get_study(job_id)
    assert actual.save_knowledge_section(job_id, f, 1)["unchanged"]
    assert actual.get_study(job_id) == before
    assert "visual_acquisitions" not in before
    new_f, _ = add_text_assessments(f.model_copy(deep=True), a.model_copy(deep=True))
    with pytest.raises(WorkflowError, match="visual_policy_required"):
        actual.save_knowledge_section(job_id, new_f, 1)
    with pytest.raises(WorkflowError, match="visual_policy_required"):
        actual.record_visual_acquisition(job_id, receipt(new_f))
    if profile_name == "study_topic_v3":
        preview = actual.prepare_document_preview(job_id, make_plan(f, a), 0)
        assert "visual_assessments" not in preview
        review(actual, job_id, preview)
        draft = promote(actual, job_id, preview)
        approve_v2(actual, job_id, draft)
        approved = actual.get_study(job_id)
        # Simulate a real v3 database without the new table, then migrate and restore exports.
        with actual.db.connect(write=True) as db:
            db.execute("DROP TABLE visual_acquisitions")
            db.execute("PRAGMA user_version=3")
        restarted = StudyService(service.root)
        assert restarted.get_study(job_id) == approved
        with sqlite3.connect(
            next(restarted.db.directory.glob("backups/schema-v3-*/study.sqlite3"))
        ) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        again = restarted.prepare_document_preview(job_id, make_plan(f, a), 1)
        assert again["idempotent"] and again["presentation_hash"] == preview["presentation_hash"]
        assert restarted.get_study(job_id)["reviews"] == approved["reviews"]
        intent = restarted.prepare_publication(job_id, draft["version"])
        assert intent["action"] == "create_page"
        assert (
            restarted.prepare_publication(job_id, draft["version"])["action"]
            == "inspect_before_retry"
        )


@pytest.mark.parametrize("role", ["research", "foundation", "advanced", "notion_writer"])
async def test_only_main_can_write_acquisition_receipts(visual_job, role):
    service, job_id, f, a = visual_job
    save_pair(service, job_id, f, a)
    server = create_server(service.root, role)
    with pytest.raises(Exception, match="Unknown tool"):
        await server.call_tool(
            "record_visual_acquisition",
            {"job_id": job_id, "content": receipt(f).model_dump(mode="json")},
        )
    if role in ("foundation", "advanced"):
        before = service.get_study(job_id)
        result = await server.call_tool("get_visual_acquisitions", {"job_id": job_id})
        assert result[1]["results"] == []
        assert service.get_study(job_id) == before


async def test_main_mcp_records_actual_receipt_without_editing_body(visual_job):
    service, job_id, f, a = visual_job
    save_pair(service, job_id, f, a)
    server = create_server(service.root, "main")
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert not tools["record_visual_acquisition"].annotations.readOnlyHint
    assert tools["get_visual_acquisitions"].annotations.readOnlyHint
    before = service.get_knowledge_section(job_id, "foundation")
    await server.call_tool(
        "record_visual_acquisition",
        {"job_id": job_id, "content": receipt(f).model_dump(mode="json")},
    )
    assert service.get_study(job_id)["visual_acquisitions"][0]["outcome"] == "unavailable"
    assert service.get_knowledge_section(job_id, "foundation") == before


def test_optional_misconception_assessment_note_renders_once(visual_job):
    service, job_id, f, a = visual_job
    item = f.misconceptions[0]
    assessment = f.visual_assessments[0].model_copy(deep=True)
    assessment.id = "misconception-assessment"
    assessment.item_id = assessment.explanation_item_id = item.id
    assessment.reader_note = "오해 항목에 연결한 실습 전제입니다."
    f.visual_assessments.append(assessment)
    save_pair(service, job_id, f, a)
    preview = service.prepare_document_preview(job_id, make_plan(f, a), 0)
    assert preview["markdown"].count(assessment.reader_note) == 1
    assert preview["notion_markdown"].count(assessment.reader_note) == 1
    review(service, job_id, preview)


def test_advanced_case_acquisition_uses_its_own_target_and_version(visual_job):
    service, job_id, f, a = visual_job
    assessment = image_request(a)
    save_pair(service, job_id, f, a)
    service.record_visual_acquisition(job_id, receipt(a, section="advanced"))
    assessment.result_ids = ["result-1"]
    assessment.status = "resolved"
    assessment.selected_kind = "text"
    assessment.explanation_item_id = assessment.item_id
    assessment.change_reason = (
        "실제 사례 시스템의 화면에 접근할 수 없어 확인된 사실과 동작을 본문으로 설명한다."
    )
    assessment.reader_note = (
        "해당 시스템의 실제 화면을 확보하지 못해 원문으로 확인된 사실을 설명합니다."
    )
    assert service.save_knowledge_section(job_id, f, 1)["unchanged"]
    service.save_knowledge_section(job_id, a, 1)
    preview = service.prepare_document_preview(job_id, make_plan(f, a, advanced_version=2), 0)
    case = next(item for item in preview["visual_assessments"] if item["section"] == "advanced")
    assert case["acquisition_results"][0]["section"] == "advanced"
    assert preview["markdown"].count(assessment.reader_note) == 1
    review(service, job_id, preview)


def test_acquired_result_checks_registered_file_bytes(visual_job):
    service, job_id, f, a = visual_job
    image_request(f)
    save_pair(service, job_id, f, a)
    path = service.root / "screen-fixture.png"
    path.write_bytes(PNG)
    asset = service.register_visual_asset(job_id, str(path))
    (service.root / asset["path"]).write_bytes(PNG + b"changed")
    with pytest.raises(WorkflowError, match="asset_integrity"):
        service.record_visual_acquisition(
            job_id,
            receipt(
                f,
                outcome="acquired",
                constraint=None,
                asset_hash=asset["hash"],
                screenshot=capture(),
            ),
        )
