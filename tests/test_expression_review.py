"""Synthetic Korean review contracts; no live research or semantic quality claim."""

from copy import deepcopy
from pathlib import Path

import pytest
from conftest import approve, draft_input, review_both, sample_source
from pydantic import ValidationError
from test_presentation_workflow import approve_v2, review_and_draft
from test_presentation_workflow import readable_sections as old_sections
from test_topic_workflow import make_plan, promote, readable_sections, review
from visual_helpers import review_refs

from cs_study_mcp.models import CrossReview, Finding, Placement, Visual
from cs_study_mcp.presentation import profile_snapshot
from cs_study_mcp.server import create_server
from cs_study_mcp.service import StudyService, WorkflowError
from cs_study_mcp.storage import dumps


@pytest.fixture
def expression_job(service):
    actual = StudyService(service.root)
    job_id = actual.create_study("합성 한국어 검수 시험")["job_id"]
    source = sample_source()
    source.evidence[0].excerpt = "Caching can improve performance when requests reuse data."
    source.evidence[0].locator = "Synthetic English fixture, paragraph 1"
    actual.save_research(job_id, [source])
    f, a = readable_sections()
    f.principles[0].body = "캐시는 반복 요청에서 성능을 개선한다."
    actual.save_knowledge_section(job_id, f, 0)
    actual.save_knowledge_section(job_id, a, 0)
    plan = make_plan(f, a)
    return actual, job_id, f, a, plan


def prepare(job):
    service, job_id, _, _, plan = job
    return service.prepare_document_preview(job_id, plan, 0)


def review_content(preview, reviewer="foundation", **changes):
    plan = preview["content"]
    data = {
        "reviewer": reviewer,
        "foundation_version": plan["foundation_version"],
        "advanced_version": plan["advanced_version"],
        "research_revision": plan["research_revision"],
        "presentation_hash": preview["presentation_hash"],
        "summary": "실제 언어 평가가 아닌 합성 검토 기록",
        "reviewed_visual_assessments": review_refs(preview),
        "korean_expression_review": {
            "policy_version": 1,
            "focus": "readability" if reviewer == "foundation" else "meaning",
            "reviewed_targets": [
                item["ref"] for item in preview["korean_expression_targets"]
            ],
        },
    }
    return CrossReview.model_validate({**data, **changes})


def body_target(preview, item_id):
    return next(
        item
        for item in preview["korean_expression_targets"]
        if item["ref"] == {"scope": "foundation", "item_id": item_id, "field": "body"}
    )


def finding(target, *, severity="blocking", problem_kind="meaning_change", **changes):
    detail = {
        "target": target["ref"],
        "problem_kind": problem_kind,
        "current_text": "성능을 개선한다",
        "suggested_text": "반복 요청에서는 성능을 개선할 수 있다",
        "evidence_refs": [{"source_id": "S1", "evidence_index": 0}],
        **changes,
    }
    return Finding.model_validate(
        {
            "severity": severity,
            "location": "기초 원리 본문",
            "comment": "합성 예시에서 가능성 표현의 검토 의견을 기록한다.",
            "expression_detail": detail,
        }
    )


def test_policy_is_new_v3_only_and_frozen_per_job(expression_job, monkeypatch):
    service, job_id, _, _, _ = expression_job
    profile = service.get_study(job_id)["presentation_profile"]
    assert profile["korean_expression_review_version"] == 1
    for name in ("legacy_v1", "study_readable_v2"):
        assert "korean_expression_review_version" not in profile_snapshot(name)
    changed = deepcopy(profile)
    changed["korean_expression_review_version"] = 99
    monkeypatch.setattr("cs_study_mcp.service.profile_snapshot", lambda _: changed)
    assert service.get_study(job_id)["presentation_profile"] == profile


def test_candidate_exposes_exact_review_text_and_source_links(expression_job):
    service, job_id, f, _, _ = expression_job
    preview = prepare(expression_job)
    targets = preview["korean_expression_targets"]
    assert targets and len({dumps(item["ref"]) for item in targets}) == len(targets)
    body = body_target(preview, f.principles[0].id)
    assert body["text"] == f.principles[0].body
    assert body["source_ids"] == ["S1"]
    assert service.get_document_preview(job_id, 1)["korean_expression_targets"] == targets
    assert not any("excerpt" in item["ref"]["field"] for item in targets)


def test_extraction_covers_reader_fields_and_excludes_internal_metadata(expression_job):
    from cs_study_mcp.expression_review import expression_targets

    service, job_id, f, a, plan = expression_job
    f.visuals = [
        Visual(
            id="diagram",
            kind="flow",
            title="표현 검토용 그림 제목",
            caption="그림의 독자용 캡션",
            alt_text="그림의 한국어 대체 설명",
            diagram_spec="flowchart LR\nA[원문 코드 노드] --> B",
            claim_ids=["F1"],
            placement=Placement(section="foundation", after_id=f.principles[0].id),
        )
    ]
    f.visual_assessments[0].reader_note = "독자에게 필요한 실습 전제"
    f.visual_assessments[0].rationale = "내부 판단 사유는 검수 본문에 추가하지 않는다"
    profile = service.get_study(job_id)["presentation_profile"]
    targets = expression_targets(plan, f, a, profile)
    refs = {(item["ref"]["scope"], item["ref"]["item_id"], item["ref"]["field"]) for item in targets}
    assert {
        ("document", None, "title"),
        ("document", None, "summary.0.text"),
        ("document", plan.outline[0].id, "title"),
        ("document", plan.outline[0].children[0].id, "title"),
        ("foundation", None, "objectives.0"),
        ("foundation", None, "prerequisites.0"),
        ("foundation", f.terms[0].id, "definition"),
        ("foundation", f.principles[0].id, "key_point"),
        ("foundation", f.examples[0].id, "body"),
        ("foundation", f.misconceptions[0].id, "body"),
        ("foundation", f.comparisons[0].id, "columns.0"),
        ("foundation", f.comparisons[0].id, "rows.0.cells.1"),
        ("foundation", "diagram", "title"),
        ("foundation", "diagram", "caption"),
        ("foundation", "diagram", "alt_text"),
        ("foundation", f.visual_assessments[0].id, "reader_note"),
        ("foundation", "FQ1", "question"),
        ("foundation", "FQ1", "answer"),
        ("foundation", "FQ1", "explanation"),
        ("advanced", a.concepts[0].id, "body"),
        ("advanced", a.tradeoffs[0].id, "choose_when"),
        ("advanced", a.cases[0].id, "limitations"),
        ("advanced", "AQ1", "answer"),
    } <= refs
    combined = "\n".join(item["text"] for item in targets)
    assert "내부 판단 사유" not in combined and "원문 코드 노드" not in combined
    assert not any("verification_note" in ref[2] for ref in refs)


def test_review_text_preserves_prose_and_link_labels_without_code_or_urls():
    from cs_study_mcp.expression_review import reviewable_text

    text = (
        "첫 설명에서 `CACHE_KEY`를 사용한다.\n"
        "[한국어 링크 설명](https://example.org/path)과 https://example.org/raw 를 본다.\n"
        "```http\nGET /secret HTTP/1.1\n```\n"
        "<pre>HIDDEN_PRE</pre><code>HIDDEN_INLINE</code>\n마지막 설명."
    )
    clean = reviewable_text(text)
    assert "첫 설명" in clean and "한국어 링크 설명" in clean and "마지막 설명" in clean
    for excluded in ("CACHE_KEY", "https://", "GET /secret", "HIDDEN_PRE", "HIDDEN_INLINE"):
        assert excluded not in clean
    assert reviewable_text("```python\nprint('code only')\n```\nhttps://example.org") == ""


def test_code_only_field_does_not_create_review_target(expression_job):
    from cs_study_mcp.expression_review import expression_targets

    service, job_id, f, a, plan = expression_job
    f.principles[0].body = "```http\nGET /example HTTP/1.1\n```"
    targets = expression_targets(plan, f, a, service.get_study(job_id)["presentation_profile"])
    assert not any(
        item["ref"]["item_id"] == f.principles[0].id and item["ref"]["field"] == "body"
        for item in targets
    )


@pytest.mark.parametrize("quote,fence", [("> ", "~~~"), ("> > ", "```")])
def test_quoted_code_preserves_surrounding_prose_without_reviewing_payload(quote, fence):
    from cs_study_mcp.expression_review import reviewable_text

    text = (
        f"{quote}앞선 인용 설명.\n"
        f"{quote}{fence}python\n{quote}print('QUOTED_CODE')\n{quote}{fence}\n"
        f"{quote}뒤따르는 인용 설명."
    )
    clean = reviewable_text(text)
    assert f"{quote}앞선 인용 설명." in clean
    assert f"{quote}뒤따르는 인용 설명." in clean
    assert "QUOTED_CODE" not in clean and "python" not in clean and fence not in clean
    assert reviewable_text(
        f"{quote}{fence}python\n{quote}print('QUOTED_CODE')\n{quote}{fence}"
    ) == ""


def quoted_body_preview(expression_job, text):
    service, job_id, f, a, _ = expression_job
    f.principles[0].body = text
    service.save_knowledge_section(job_id, f, 1)
    a.foundation_version = 2
    service.save_knowledge_section(job_id, a, 1)
    return service.prepare_document_preview(
        job_id, make_plan(f, a, foundation_version=2, advanced_version=2), 0
    )


@pytest.mark.parametrize("quote,fence", [("> ", "~~~"), ("> > ", "```")])
def test_code_only_quoted_body_has_no_candidate_review_target(expression_job, quote, fence):
    _, _, f, _, _ = expression_job
    text = f"{quote}{fence}python\n{quote}print('QUOTED_CODE')\n{quote}{fence}"
    preview = quoted_body_preview(expression_job, text)
    assert "QUOTED_CODE" in preview["markdown"]
    assert not any(
        item["ref"]["item_id"] == f.principles[0].id and item["ref"]["field"] == "body"
        for item in preview["korean_expression_targets"]
    )


@pytest.mark.parametrize("quote,fence", [("> ", "~~~"), ("> > ", "```")])
def test_service_rejects_quoted_code_as_expression_problem_text(expression_job, quote, fence):
    service, job_id, f, _, _ = expression_job
    text = (
        f"{quote}앞선 인용 설명.\n"
        f"{quote}{fence}python\n{quote}print('QUOTED_CODE')\n{quote}{fence}\n"
        f"{quote}뒤따르는 인용 설명."
    )
    preview = quoted_body_preview(expression_job, text)
    target = body_target(preview, f.principles[0].id)
    assert "QUOTED_CODE" in preview["markdown"] and "QUOTED_CODE" not in target["text"]
    invalid = finding(
        target,
        severity="advisory",
        problem_kind="readability",
        current_text="print('QUOTED_CODE')",
        evidence_refs=[],
    )
    with pytest.raises(WorkflowError, match="expression_text_mismatch"):
        service.record_cross_review(job_id, review_content(preview, findings=[invalid]))
    assert service.get_study(job_id)["cross_reviews"] == []


@pytest.mark.parametrize("change", ["absent", "empty", "missing", "duplicate", "unknown", "focus", "policy"])
def test_review_requires_correct_focus_and_complete_exact_target_coverage(expression_job, change):
    service, job_id, _, _, _ = expression_job
    preview = prepare(expression_job)
    content = review_content(preview).model_dump(mode="json")
    contract = content["korean_expression_review"]
    if change == "absent":
        content.pop("korean_expression_review")
    elif change == "empty":
        contract["reviewed_targets"] = []
    elif change == "missing":
        contract["reviewed_targets"].pop()
    elif change == "duplicate":
        contract["reviewed_targets"].append(contract["reviewed_targets"][0])
    elif change == "unknown":
        contract["reviewed_targets"][0] = {
            "scope": "foundation", "item_id": "absent", "field": "body"
        }
    elif change == "focus":
        contract["focus"] = "meaning"
    else:
        contract["policy_version"] = 2
    with pytest.raises((WorkflowError, ValidationError)):
        service.record_cross_review(job_id, CrossReview.model_validate(content))
    assert service.get_study(job_id)["cross_reviews"] == []


def test_advanced_review_requires_meaning_focus(expression_job):
    service, job_id, _, _, _ = expression_job
    preview = prepare(expression_job)
    content = review_content(preview, "advanced").model_dump(mode="json")
    content["korean_expression_review"]["focus"] = "readability"
    with pytest.raises(WorkflowError, match="expression_review_focus"):
        service.record_cross_review(job_id, CrossReview.model_validate(content))


@pytest.mark.parametrize("change", ["target", "text", "source", "index", "missing_evidence"])
def test_expression_findings_validate_actual_candidate_and_original_evidence(expression_job, change):
    service, job_id, f, _, _ = expression_job
    preview = prepare(expression_job)
    target = body_target(preview, f.principles[0].id)
    detail = finding(target).model_dump(mode="json")
    expression = detail["expression_detail"]
    if change == "target":
        expression["target"]["item_id"] = "absent"
    elif change == "text":
        expression["current_text"] = "후보에 존재하지 않는 문장"
    elif change == "source":
        expression["evidence_refs"][0]["source_id"] = "Unknown"
    elif change == "index":
        expression["evidence_refs"][0]["evidence_index"] = 1
    else:
        expression["evidence_refs"] = []
    with pytest.raises((WorkflowError, ValidationError)):
        service.record_cross_review(
            job_id, review_content(preview, "advanced", findings=[detail])
        )


@pytest.mark.parametrize("severity", ["blocking", "advisory"])
def test_language_finding_uses_existing_blocking_and_advisory_gate(expression_job, severity):
    service, job_id, f, _, _ = expression_job
    preview = prepare(expression_job)
    target = body_target(preview, f.principles[0].id)
    service.record_cross_review(job_id, review_content(preview))
    result = service.record_cross_review(
        job_id,
        review_content(
            preview, "advanced",
            findings=[finding(target, severity=severity, problem_kind="readability", evidence_refs=[])],
        ),
    )
    assert result["passed"] == (severity == "advisory")
    if severity == "blocking":
        with pytest.raises(WorkflowError, match="cross_review_required"):
            promote(service, job_id, preview)
    else:
        assert promote(service, job_id, preview)["version"] == 1


def test_readability_advisory_does_not_require_english_evidence(expression_job):
    service, job_id, f, _, _ = expression_job
    preview = prepare(expression_job)
    target = body_target(preview, f.principles[0].id)
    result = service.record_cross_review(
        job_id,
        review_content(
            preview,
            findings=[finding(target, severity="advisory", problem_kind="readability", evidence_refs=[])],
        ),
    )
    assert result["passed"]


def test_meaning_change_cannot_be_downgraded_to_advisory(expression_job):
    service, job_id, f, _, _ = expression_job
    preview = prepare(expression_job)
    target = body_target(preview, f.principles[0].id)
    with pytest.raises(WorkflowError, match="expression_meaning_severity"):
        service.record_cross_review(
            job_id,
            review_content(preview, "advanced", findings=[finding(target, severity="advisory")]),
        )


def test_expression_only_edit_invalidates_candidate_reviews_and_user_approval(expression_job):
    service, job_id, f, a, plan = expression_job
    preview = prepare(expression_job)
    review(service, job_id, preview)
    draft = promote(service, job_id, preview)
    approve_v2(service, job_id, draft)
    f.principles[0].body = "캐시는 반복 요청에서 성능을 개선할 수 있다."
    service.save_knowledge_section(job_id, f, 1)
    a.foundation_version = 2
    service.save_knowledge_section(job_id, a, 1)
    updated_plan = make_plan(f, a, foundation_version=2, advanced_version=2)
    updated = service.prepare_document_preview(job_id, updated_plan, 1)
    assert updated["presentation_hash"] != preview["presentation_hash"]
    assert updated["korean_expression_targets"] != preview["korean_expression_targets"]
    assert not service.get_document_preview(job_id, 1)["current"]
    assert service.get_document_preview(job_id, 1)["korean_expression_targets"] == preview["korean_expression_targets"]
    with pytest.raises(WorkflowError):
        service.prepare_publication(job_id, draft["version"])
    with pytest.raises(WorkflowError):
        service.record_cross_review(job_id, review_content(preview))
    with pytest.raises(WorkflowError):
        promote(service, job_id, updated)
    review(service, job_id, updated)
    assert promote(service, job_id, updated)["markdown"] != preview["markdown"]
    assert plan.title == updated_plan.title


def test_identical_candidate_repairs_export_without_changing_targets_or_approval(expression_job):
    service, job_id, _, _, plan = expression_job
    preview = prepare(expression_job)
    review(service, job_id, preview)
    draft = promote(service, job_id, preview)
    approve_v2(service, job_id, draft)
    approved = service.get_study(job_id)
    Path(preview["paths"]["markdown"]).write_text("손상된 파일", encoding="utf-8")
    restarted = StudyService(service.root)
    repaired = restarted.prepare_document_preview(job_id, plan, 1)
    assert repaired["idempotent"] and repaired["current"]
    assert repaired["presentation_hash"] == preview["presentation_hash"]
    assert repaired["korean_expression_targets"] == preview["korean_expression_targets"]
    assert restarted.get_study(job_id) == approved
    restarted.validate_publication_bundle(job_id)


def test_optional_review_and_finding_fields_preserve_historical_serialization():
    historical = {
        "reviewer": "foundation",
        "foundation_version": 1,
        "advanced_version": 1,
        "research_revision": 1,
        "summary": "기존 검토",
        "findings": [{"severity": "advisory", "location": "본문", "comment": "기존 권고"}],
        "presentation_hash": None,
    }
    assert CrossReview.model_validate(historical).model_dump(mode="json") == historical
    assert Finding.model_validate(historical["findings"][0]).model_dump(mode="json") == historical["findings"][0]


@pytest.mark.parametrize("name", ["legacy_v1", "study_readable_v2", "study_topic_v3"])
def test_policy_absent_jobs_keep_existing_review_hash_and_approval_contract(service, monkeypatch, name):
    old_profile = profile_snapshot(name)
    old_profile.pop("korean_expression_review_version", None)
    old_profile.get("guidance", {}).pop("korean_expression_review", None)
    with monkeypatch.context() as context:
        context.setattr("cs_study_mcp.service.profile_snapshot", lambda _: old_profile)
        actual = StudyService(service.root)
        job_id = actual.create_study("정책 이전 작업", presentation_profile=name)["job_id"]
    actual.save_research(job_id, [sample_source()])
    f, a = readable_sections() if name == "study_topic_v3" else old_sections()
    if name == "legacy_v1":
        f.comparisons = []
    actual.save_knowledge_section(job_id, f, 0)
    actual.save_knowledge_section(job_id, a, 0)
    if name == "legacy_v1":
        review_both(actual, job_id)
        draft = actual.save_draft(job_id, draft_input())
        approve(actual, job_id, draft)
    elif name == "study_readable_v2":
        draft = review_and_draft(actual, job_id)
        approve_v2(actual, job_id, draft)
    else:
        preview = actual.prepare_document_preview(job_id, make_plan(f, a), 0)
        assert "korean_expression_targets" not in preview
        review(actual, job_id, preview)
        draft = promote(actual, job_id, preview)
        approve_v2(actual, job_id, draft)
    before = actual.get_study(job_id)
    assert "korean_expression_review_version" not in before["presentation_profile"]
    assert actual.save_knowledge_section(job_id, f, 1)["unchanged"]
    restarted = StudyService(service.root)
    assert restarted.get_study(job_id) == before
    assert "korean_expression_review" not in dumps(before["cross_reviews"])
    assert restarted.prepare_publication(job_id, draft["version"])["action"] == "create_page"
    assert restarted.prepare_publication(job_id, draft["version"])["action"] == "inspect_before_retry"


async def test_mcp_review_schema_and_readonly_candidate_expose_new_contract(expression_job):
    service, job_id, _, _, _ = expression_job
    preview = prepare(expression_job)
    server = create_server(service.root, "foundation")
    tools = {tool.name: tool for tool in await server.list_tools()}
    schema = dumps(tools["record_cross_review"].inputSchema)
    assert "korean_expression_review" in schema and "expression_detail" in schema
    assert "reviewed_targets" in schema and "evidence_index" in schema
    assert tools["get_document_preview"].annotations.readOnlyHint
    before = service.get_study(job_id)
    readback = await server.call_tool("get_document_preview", {"job_id": job_id, "version": 1})
    assert readback[1]["korean_expression_targets"] == preview["korean_expression_targets"]
    assert service.get_study(job_id) == before
    result = await server.call_tool(
        "record_cross_review",
        {"job_id": job_id, "review": review_content(preview).model_dump(mode="json")},
    )
    assert result[1]["passed"]
