import pytest
from conftest import PAGE_ID, PARENT_ID, approve, sample_source

from cs_study_mcp.models import CrossReview, PublicationInput, ReviewInput
from cs_study_mcp.render import normalize_markdown
from cs_study_mcp.service import WorkflowError


def test_late_cross_review_does_not_claim_new_research_revision(populated):
    service, job_id = populated
    old_review = CrossReview(
        reviewer="foundation",
        foundation_version=1,
        advanced_version=1,
        research_revision=1,
        summary="이전 자료만 읽고 완료한 검토",
    )
    service.save_research(job_id, [sample_source("S2")])
    with pytest.raises(WorkflowError, match="stale_research"):
        service.record_cross_review(job_id, old_review)


def test_approval_cannot_silently_switch_displayed_target(reviewed):
    service, job_id, draft = reviewed
    (service.root / "study.local.toml").write_text(
        f'[notion]\nparent_page_id="{PAGE_ID}"', encoding="utf-8"
    )
    with pytest.raises(WorkflowError, match="target_changed"):
        service.record_review(
            job_id,
            ReviewInput(
                draft_version=draft["version"],
                draft_hash=draft["hash"],
                parent_page_id=PARENT_ID,
                decision="approve",
                user_message="표시된 A 페이지 아래 발행해 주세요.",
            ),
        )


def test_inflight_publication_retains_original_parent_when_config_changes(reviewed):
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
    (service.root / "study.local.toml").write_text(
        f'[notion]\nparent_page_id="{PAGE_ID}"', encoding="utf-8"
    )
    resume = service.prepare_publication(job_id, draft["version"])
    assert resume["action"] == "resume_existing_page"
    assert resume["parent_page_id"] == PARENT_ID


@pytest.mark.parametrize(
    "first,second",
    [
        ("- parent\n  - child", "- parent\n- child"),
        ("    code()", "code()"),
    ],
)
def test_readback_preserves_structural_indentation(first, second):
    assert normalize_markdown(first) != normalize_markdown(second)


def test_duplicate_create_receipt_cannot_erase_failed_readback(reviewed):
    service, job_id, draft = reviewed
    approve(service, job_id, draft)
    intent = service.prepare_publication(job_id, draft["version"])
    receipt = PublicationInput(
        attempt_id=intent["attempt_id"], outcome="page_created", page_id=PAGE_ID
    )
    service.record_publication(job_id, receipt)
    service.record_publication(
        job_id,
        PublicationInput(
            attempt_id=intent["attempt_id"],
            outcome="verified",
            observed_title=intent["title"],
            observed_parent_page_id=PARENT_ID,
            observed_markdown="잘못된 본문",
        ),
    )
    result = service.record_publication(job_id, receipt)
    assert result["idempotent"]
    assert result["status"] == "needs_attention"
    assert result["error"]


def test_correct_body_under_wrong_parent_does_not_complete(reviewed):
    service, job_id, draft = reviewed
    approve(service, job_id, draft)
    intent = service.prepare_publication(job_id, draft["version"])
    result = service.record_publication(
        job_id,
        PublicationInput(
            attempt_id=intent["attempt_id"],
            outcome="verified",
            page_id=PAGE_ID,
            observed_title=intent["title"],
            observed_markdown=intent["markdown"],
            observed_parent_page_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ),
    )
    assert result["status"] == "needs_attention"
