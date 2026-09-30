from datetime import UTC, datetime

import pytest

from cs_study_mcp.models import (
    AdvancedSection,
    Claim,
    CrossReview,
    DraftInput,
    Evidence,
    Explanation,
    FoundationSection,
    Illustration,
    Question,
    RealCase,
    ReviewInput,
    Source,
    SummaryPoint,
    Term,
    Tradeoff,
)
from cs_study_mcp.service import StudyService


class LegacyStudyService(StudyService):
    """Keep the original workflow suite exercising the unchanged v1 contract."""

    def create_study(self, *args, **kwargs):
        kwargs.setdefault("presentation_profile", "legacy_v1")
        return super().create_study(*args, **kwargs)

PARENT_ID = "3d066fe0-f687-809e-934c-d6ae7facf745"
PAGE_ID = "11111111-2222-3333-4444-555555555555"


def sample_source(source_id="S1"):
    """Synthetic fixture only; not a claim of live web verification."""
    return Source(
        id=source_id,
        url="https://example.org/test-fixture",
        title="모의 원문",
        kind="official",
        accessed_at=datetime(2026, 9, 29, tzinfo=UTC),
        original_checked=True,
        evidence=[Evidence(excerpt="테스트 전용 근거입니다.", locator="모의 문단 1")],
    )


def sample_sections(topic="테스트 주제", foundation_version=1):
    foundation_claim = Claim(
        id="F1",
        text=f"{topic} 기초 모의 주장",
        source_ids=["S1"],
        verdict="supported",
        verification_note="자동 테스트용 판정이며 실제 지식 검증이 아닙니다.",
    )
    advanced_claim = foundation_claim.model_copy(
        update={"id": "A1", "text": f"{topic} 심화 모의 주장"}
    )
    foundation = FoundationSection(
        objectives=[f"{topic}의 기초를 설명한다."],
        prerequisites=["컴퓨터 구조 기본 용어"],
        terms=[
            Term(
                name=topic,
                definition="모의 정의",
                why="모의 필요성",
                example="간단한 예시",
                claim_ids=["F1"],
            )
        ],
        principles=[Explanation(heading="기본 원리", body="모의 동작 설명", claim_ids=["F1"])],
        examples=[Illustration(title="설명용 상황", body="가상 예시입니다.")],
        misconceptions=[Explanation(heading="흔한 오해", body="모의 오해 정정", claim_ids=["F1"])],
        claims=[foundation_claim],
        questions=[
            Question(
                id=f"FQ{i}",
                kind=kind,
                question=f"{topic} 기초 질문 {i}?",
                answer="모의 답안",
                explanation="모의 해설",
                claim_ids=["F1"],
            )
            for i, kind in enumerate(["concept", "concept", "comparison"], 1)
        ],
    )
    advanced = AdvancedSection(
        foundation_version=foundation_version,
        concepts=[
            Explanation(heading="심화 동작", body="조건을 명시한 모의 설명", claim_ids=["A1"])
        ],
        tradeoffs=[
            Tradeoff(
                topic="선택 기준",
                options="A 또는 B",
                advantages="모의 장점",
                limitations="모의 한계",
                choose_when="모의 적용 조건",
                claim_ids=["A1"],
            )
        ],
        cases=[
            RealCase(
                title="모의 사례 fixture",
                system="모의 시스템",
                problem="모의 문제",
                technology="모의 기술",
                rationale="모의 이유",
                outcome="모의 결과",
                limitations="실제 사례 검증이 아님",
                interpretation="모의 학습 해석",
                claim_ids=["A1"],
            )
        ],
        claims=[advanced_claim],
        questions=[
            Question(
                id=f"AQ{i}",
                kind=kind,
                question=f"{topic} 심화 질문 {i}?",
                answer="모의 심화 답안",
                explanation="모의 해설",
                claim_ids=["A1"],
            )
            for i, kind in enumerate(["comparison", "application", "application"], 1)
        ],
    )
    return foundation, advanced


def review_both(service, job_id, foundation_version=1, advanced_version=1):
    for role in ("foundation", "advanced"):
        service.record_cross_review(
            job_id,
            CrossReview(
                reviewer=role,
                foundation_version=foundation_version,
                advanced_version=advanced_version,
                research_revision=service.get_study(job_id)["research_revision"],
                summary="자동 테스트에서 생성한 모의 교차 검토",
            ),
        )


def draft_input(topic="테스트 주제", foundation_version=1, advanced_version=1):
    return DraftInput(
        title=topic,
        foundation_version=foundation_version,
        advanced_version=advanced_version,
        summary=[
            SummaryPoint(text="기초와 심화의 모의 요약", section="foundation", claim_ids=["F1"])
        ],
    )


def approve(service, job_id, draft):
    return service.record_review(
        job_id,
        ReviewInput(
            draft_version=draft["version"],
            draft_hash=draft["hash"],
            parent_page_id=service.get_study(job_id)["configured_parent_page_id"] or PARENT_ID,
            decision="approve",
            user_message="테스트 fixture: 이 초안을 발행해 주세요.",
        ),
    )


@pytest.fixture
def service(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# 테스트용 공통 규칙\n승인 후 발행\n", encoding="utf-8")
    (tmp_path / "study.local.toml").write_text(
        f'[notion]\nparent_page_id = "{PARENT_ID}"\n', encoding="utf-8"
    )
    return LegacyStudyService(tmp_path)


@pytest.fixture
def populated(service):
    job = service.create_study("테스트 주제")
    service.save_research(job["job_id"], [sample_source()])
    f, a = sample_sections()
    service.save_knowledge_section(job["job_id"], f, 0)
    service.save_knowledge_section(job["job_id"], a, 0)
    return service, job["job_id"]


@pytest.fixture
def reviewed(populated):
    service, job_id = populated
    review_both(service, job_id)
    draft = service.save_draft(job_id, draft_input())
    return service, job_id, draft
