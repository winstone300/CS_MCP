import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import (
    PAGE_ID,
    PARENT_ID,
    approve,
    draft_input,
    review_both,
    sample_sections,
    sample_source,
)

from cs_study_mcp.models import CrossReview, Finding, PublicationInput, ReviewInput
from cs_study_mcp.render import normalize_markdown
from cs_study_mcp.service import StudyService, WorkflowError
from cs_study_mcp.settings import notion_id


def test_rules_snapshot_survives_edits(service):
    first = service.create_study("첫 작업")
    (service.root / "AGENTS.md").write_text("새 규칙", encoding="utf-8")
    second = service.create_study("둘째 작업")
    assert first["rules_hash"] != second["rules_hash"]
    assert service.get_study(first["job_id"])["rules_text"] == first["rules_text"]


def test_missing_rules_blocks_create(service):
    (service.root / "AGENTS.md").unlink()
    with pytest.raises(WorkflowError, match="missing_rules"):
        service.create_study("없는 규칙")


def test_sources_immutable_and_atomic(populated):
    service, job_id = populated
    changed = sample_source().model_copy(update={"title": "변경된 출처"})
    with pytest.raises(WorkflowError, match="source_immutable"):
        service.save_research(job_id, [sample_source("S2"), changed])
    assert [s["id"] for s in service.get_study(job_id)["sources"]] == ["S1"]
    assert service.save_research(job_id, [sample_source()])["inserted"] == []


@pytest.mark.parametrize(
    "problem,expected",
    [
        ("missing_source", "unknown_source"),
        ("no_evidence", "missing_evidence"),
        ("uncertain", "unresolved_claim"),
        ("missing_case", "missing_real_case"),
        ("case_reference", "missing_case_evidence"),
        ("question_count", "question_distribution"),
        ("duplicate_question", "duplicate_question"),
    ],
)
def test_invalid_content_cannot_form_draft(populated, problem, expected):
    service, job_id = populated
    f, a = sample_sections()
    if problem == "missing_source":
        a.claims[0].source_ids = ["UNKNOWN"]
    elif problem == "no_evidence":
        a.claims[0].source_ids = []
    elif problem == "uncertain":
        a.claims[0].verdict = "uncertain"
    elif problem == "missing_case":
        a.cases = []
    elif problem == "case_reference":
        a.cases[0].claim_ids = []
    elif problem == "question_count":
        a.questions.pop()
    else:
        a.questions[0].question = f.questions[0].question
    service.save_knowledge_section(job_id, a, 1)
    report = service.validate_knowledge(job_id)
    assert not report["valid"]
    assert expected in {issue["code"] for issue in report["issues"]}
    with pytest.raises(WorkflowError, match="validation_failed"):
        service.save_draft(job_id, draft_input(advanced_version=2))


def test_version_conflicts_preserve_other_section(populated):
    service, job_id = populated
    f, _ = sample_sections()
    f.objectives.append("새 목표")
    service.save_knowledge_section(job_id, f, 1)
    with pytest.raises(WorkflowError, match="version_conflict"):
        service.save_knowledge_section(job_id, f, 1)
    assert service.get_knowledge_section(job_id, "advanced")["version"] == 1
    assert "stale_foundation" in {i["code"] for i in service.validate_knowledge(job_id)["issues"]}


def test_concurrent_section_writes_have_one_winner(populated):
    service, job_id = populated

    def update(index):
        f, _ = sample_sections()
        f.objectives.append(f"수정 {index}")
        try:
            return service.save_knowledge_section(job_id, f, 1)["version"]
        except WorkflowError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [1, 2]))
    assert results.count(2) == 1
    assert results.count("version_conflict") == 1


def test_cross_reviews_required_and_blocking_findings(populated):
    service, job_id = populated
    with pytest.raises(WorkflowError, match="cross_review_required"):
        service.save_draft(job_id, draft_input())
    review_both(service, job_id)
    service.record_cross_review(
        job_id,
        CrossReview(
            reviewer="foundation",
            foundation_version=1,
            advanced_version=1,
            research_revision=1,
            summary="사례의 전제 충돌",
            findings=[
                Finding(
                    severity="blocking", location="advanced.cases.0", comment="조건 재검토 필요"
                )
            ],
        ),
    )
    with pytest.raises(WorkflowError, match="cross_review_required"):
        service.save_draft(job_id, draft_input())


def test_research_change_invalidates_reviews_and_approval(reviewed):
    service, job_id, draft = reviewed
    approve(service, job_id, draft)
    service.save_research(job_id, [sample_source("S2")])
    assert service.get_study(job_id)["current_draft_version"] is None
    with pytest.raises(WorkflowError, match="no_current_draft"):
        service.prepare_publication(job_id, draft["version"])
    with pytest.raises(WorkflowError, match="cross_review_required"):
        service.save_draft(job_id, draft_input())


def test_section_change_requires_new_reviews_and_approval(reviewed):
    service, job_id, old_draft = reviewed
    approve(service, job_id, old_draft)
    f, a = sample_sections(foundation_version=2)
    f.objectives.append("새 목표")
    service.save_knowledge_section(job_id, f, 1)
    service.save_knowledge_section(job_id, a, 1)
    with pytest.raises(WorkflowError, match="stale_review"):
        review_both(service, job_id)
    with pytest.raises(WorkflowError, match="cross_review_required"):
        service.save_draft(job_id, draft_input(foundation_version=2, advanced_version=2))
    review_both(service, job_id, 2, 2)
    draft = service.save_draft(job_id, draft_input(foundation_version=2, advanced_version=2))
    with pytest.raises(WorkflowError, match="approval_required"):
        service.prepare_publication(job_id, draft["version"])
    with pytest.raises(WorkflowError, match="stale_approval"):
        approve(service, job_id, old_draft)


def test_approval_required_and_changes_requested_revokes(reviewed):
    service, job_id, draft = reviewed
    with pytest.raises(WorkflowError, match="approval_required"):
        service.prepare_publication(job_id, draft["version"])
    approve(service, job_id, draft)
    service.record_review(
        job_id,
        ReviewInput(
            draft_version=draft["version"],
            draft_hash=draft["hash"],
            parent_page_id=PARENT_ID,
            decision="request_changes",
            user_message="아직 발행하지 말고 예시를 수정해 주세요.",
            feedback="예시 수정",
        ),
    )
    with pytest.raises(WorkflowError, match="approval_required"):
        service.prepare_publication(job_id, draft["version"])


def test_changed_target_needs_new_approval(reviewed):
    service, job_id, draft = reviewed
    approve(service, job_id, draft)
    (service.root / "study.local.toml").write_text(
        f'[notion]\nparent_page_id="{PAGE_ID}"\n', encoding="utf-8"
    )
    with pytest.raises(WorkflowError, match="target_changed"):
        service.prepare_publication(job_id, draft["version"])
    approve(service, job_id, draft)
    assert service.prepare_publication(job_id, draft["version"])["parent_page_id"] == PAGE_ID


def test_target_must_be_configured(reviewed):
    service, job_id, draft = reviewed
    (service.root / "study.local.toml").unlink()
    with pytest.raises(WorkflowError, match="target_not_configured"):
        approve(service, job_id, draft)


def test_followup_budget_is_global_and_idempotent(service):
    job_id = service.create_study("재검색")["job_id"]
    assert service.request_research_followup(job_id, "r1", "기초 근거")["round"] == 1
    assert service.request_research_followup(job_id, "r1", "기초 근거")["round"] == 1
    assert service.request_research_followup(job_id, "r2", "심화 사례")["round"] == 2
    assert not service.request_research_followup(job_id, "r3", "추가 사례")["allowed"]
    assert service.get_study(job_id)["status"] == "needs_attention"


def test_concurrent_publication_authorizes_create_only_once(reviewed):
    service, job_id, draft = reviewed
    approve(service, job_id, draft)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: service.prepare_publication(job_id, draft["version"]), range(2))
        )
    assert [r["action"] for r in results].count("create_page") == 1
    assert [r["action"] for r in results].count("inspect_before_retry") == 1
    assert len({r["attempt_id"] for r in results}) == 1


@pytest.mark.parametrize("failure", ["uncertain", "auth_required", "retryable_error"])
def test_unknown_create_result_never_recreates(reviewed, failure):
    service, job_id, draft = reviewed
    approve(service, job_id, draft)
    intent = service.prepare_publication(job_id, draft["version"])
    service.record_publication(
        job_id,
        PublicationInput(attempt_id=intent["attempt_id"], outcome=failure, error="모의 오류"),
    )
    restarted = StudyService(service.root)
    assert (
        restarted.prepare_publication(job_id, draft["version"])["action"] == "inspect_before_retry"
    )
    assert restarted.get_study(job_id)["status"] == "needs_attention"


def test_partial_page_recovers_by_id_and_checks_readback(reviewed):
    service, job_id, draft = reviewed
    approve(service, job_id, draft)
    intent = service.prepare_publication(job_id, draft["version"])
    service.record_publication(
        job_id,
        PublicationInput(
            attempt_id=intent["attempt_id"],
            outcome="page_created",
            page_id=PAGE_ID,
        ),
    )
    with pytest.raises(WorkflowError, match="publication_locked"):
        service.save_knowledge_section(job_id, sample_sections()[0], 1)
    restarted = StudyService(service.root)
    assert (
        restarted.prepare_publication(job_id, draft["version"])["action"] == "resume_existing_page"
    )
    mismatch = restarted.record_publication(
        job_id,
        PublicationInput(
            attempt_id=intent["attempt_id"],
            outcome="verified",
            observed_title=intent["title"],
            observed_parent_page_id=PARENT_ID,
            observed_markdown="누락된 모의 본문",
        ),
    )
    assert mismatch["status"] == "needs_attention"
    verified = restarted.record_publication(
        job_id,
        PublicationInput(
            attempt_id=intent["attempt_id"],
            outcome="verified",
            observed_title=intent["title"],
            observed_parent_page_id=PARENT_ID,
            observed_markdown=intent["markdown"].replace("\n", "\r\n"),
        ),
    )
    assert verified["status"] == "completed"
    assert restarted.prepare_publication(job_id, draft["version"])["action"] == "already_completed"


def test_publication_rejects_wrong_attempt_page_and_missing_readback(reviewed):
    service, job_id, draft = reviewed
    approve(service, job_id, draft)
    intent = service.prepare_publication(job_id, draft["version"])
    with pytest.raises(WorkflowError, match="unknown_attempt"):
        service.record_publication(
            job_id, PublicationInput(attempt_id="wrong", outcome="uncertain")
        )
    with pytest.raises(WorkflowError, match="readback_required"):
        service.record_publication(
            job_id, PublicationInput(attempt_id=intent["attempt_id"], outcome="verified")
        )
    with pytest.raises(WorkflowError, match="page_conflict"):
        service.record_publication(
            job_id,
            PublicationInput(
                attempt_id=intent["attempt_id"],
                outcome="page_created",
                page_id=PAGE_ID,
                page_url="https://example.org/not-notion",
            ),
        )


def test_markdown_normalization_preserves_code_indentation():
    fence = chr(96) * 3
    one = f"{fence}python\nif True:\n    work()\n{fence}"
    two = f"{fence}python\nif True:\nwork()\n{fence}"
    assert normalize_markdown(one) != normalize_markdown(two)
    assert normalize_markdown("## 제목\r\n\r\n내용\n<empty-block/>") == "## 제목\n내용"


def test_export_rebuilt_and_db_is_source_of_truth(reviewed):
    service, job_id, draft = reviewed
    path = Path(draft["path"])
    path.write_text("외부 파일 편집", encoding="utf-8")
    assert service.get_study(job_id)["draft"]["markdown"] == draft["markdown"]
    assert service.export_draft(job_id).read_text(encoding="utf-8") == draft["markdown"]


@pytest.mark.parametrize("topic", ["프로세스·스레드", "TCP·UDP", "데이터베이스 인덱스"])
def test_mock_topic_workflow_not_live_verification(service, topic):
    job_id = service.create_study(topic)["job_id"]
    service.save_research(job_id, [sample_source()])
    for section in sample_sections(topic):
        service.save_knowledge_section(job_id, section, 0)
    review_both(service, job_id)
    draft = service.save_draft(job_id, draft_input(topic))
    assert "## 핵심 용어" in draft["markdown"]
    assert "## 실제 사례" in draft["markdown"]
    assert len(re.findall(r"^### 기초 \d+\.", draft["markdown"], re.M)) == 3
    assert len(re.findall(r"^### 심화 \d+\.", draft["markdown"], re.M)) == 3
    assert "(가상 설명용 예시)" in draft["markdown"]
    approve(service, job_id, draft)
    intent = service.prepare_publication(job_id, draft["version"])
    result = service.record_publication(
        job_id,
        PublicationInput(
            attempt_id=intent["attempt_id"],
            outcome="verified",
            page_id=PAGE_ID,
            observed_title=intent["title"],
            observed_parent_page_id=PARENT_ID,
            observed_markdown=intent["markdown"],
        ),
    )
    assert result["status"] == "completed"


@pytest.mark.parametrize(
    "url",
    [
        "https://app.notion.com/p/CS-3d066fe0f687809e934cd6ae7facf745",
        "https://www.notion.so/CS-3d066fe0f687809e934cd6ae7facf745?pvs=4",
        "3d066fe0-f687-809e-934c-d6ae7facf745",
    ],
)
def test_notion_id_formats(url):
    assert notion_id(url) == "3d066fe0-f687-809e-934c-d6ae7facf745"
