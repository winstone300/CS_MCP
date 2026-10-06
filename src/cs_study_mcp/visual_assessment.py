"""Structural checks for authored visual decisions; usefulness stays with reviewers."""

from collections import Counter

from .models import FoundationSection
from .render import digest
from .storage import dumps


def assessment_policy(profile):
    return profile.get("visual_assessment_policy_version") == 1


def target_items(section):
    if isinstance(section, FoundationSection):
        items = [*section.terms, *section.principles, *section.examples, *section.misconceptions]
    else:
        items = [*section.concepts, *section.tradeoffs, *section.cases]
    return {item.id: item for item in items if item.id}


def target_hash(item):
    return digest(dumps(item.model_dump(mode="json")))


def request_hash(assessment):
    # Author resolution/linking does not change the original acquisition request.
    return digest(
        dumps(
            {
                key: getattr(assessment, key)
                for key in (
                    "id",
                    "item_id",
                    "learning_goal",
                    "preferred_kind",
                    "acquisition_plan",
                )
            }
        )
    )


def acquisition_hash(content, target, request):
    return digest(dumps({"content": content, "target_hash": target, "request_hash": request}))


def assessment_refs(sections):
    return [
        {"section": section.kind, "assessment_id": item.id}
        for section in sections
        if section is not None
        for item in section.visual_assessments
    ]


def assessment_issues(section, sources):
    errors = []
    manual = []
    items = target_items(section)
    visuals = {visual.id: visual for visual in section.visuals}
    claims = {claim.id: claim for claim in section.claims}
    assessments = section.visual_assessments

    def error(code, aid, message):
        errors.append(
            {"code": code, "path": f"{section.kind}.visual_assessments.{aid}", "message": message}
        )

    for identifier, count in Counter(item.id for item in assessments).items():
        if count > 1:
            error("duplicate_visual_assessment", identifier, "평가 ID가 중복됩니다.")
    for identifier, count in Counter(item.item_id for item in assessments).items():
        if count > 1:
            error(
                "duplicate_assessment_target", identifier, "본문 항목마다 평가 하나를 기록하세요."
            )
    required = section.examples if isinstance(section, FoundationSection) else section.cases
    assessed = {item.item_id for item in assessments}
    for item in required:
        if item.id not in assessed:
            error("missing_visual_assessment", item.id, "예시·실제 사례의 표현 판단이 필요합니다.")
    for assessment in assessments:
        aid = assessment.id
        if assessment.item_id not in items:
            error("unknown_assessment_target", aid, "같은 역할의 본문 항목에 연결하세요.")
        if any(sid not in sources for sid in assessment.source_ids):
            error("unknown_assessment_source", aid, "평가의 출처가 등록되지 않았습니다.")
        if any(
            cid not in claims or claims[cid].verdict != "supported" for cid in assessment.claim_ids
        ):
            error(
                "unknown_assessment_claim",
                aid,
                "독자용 설명은 해당 역할의 검증된 주장과 연결하세요.",
            )
        if assessment.status != "resolved" or assessment.selected_kind is None:
            error(
                "unresolved_visual_assessment", aid, "자료 확보·대체 설명을 반영한 뒤 완료하세요."
            )
        if assessment.selected_kind != assessment.preferred_kind and not assessment.change_reason:
            error(
                "missing_visual_change_reason",
                aid,
                "최초 판단과 다른 표현을 선택한 이유가 필요합니다.",
            )
        for field in ("visual_ids", "result_ids"):
            values = getattr(assessment, field)
            if len(values) != len(set(values)):
                error("duplicate_assessment_link", aid, "같은 자료·결과를 중복 연결할 수 없습니다.")
        if assessment.preferred_kind == "image":
            if not assessment.acquisition_plan.strip() or not assessment.result_ids:
                error(
                    "missing_acquisition_result",
                    aid,
                    "이미지 요청의 확보 계획과 메인 결과를 연결하세요.",
                )
            if assessment.selected_kind not in (None, "image") and not assessment.reader_note:
                error(
                    "missing_visual_fallback_note", aid, "대체한 이유·전제를 독자에게 설명하세요."
                )
        if assessment.selected_kind in ("image", "diagram"):
            if not assessment.visual_ids:
                error("missing_assessment_visual", aid, "선택한 실제 그림을 연결하세요.")
            for vid in assessment.visual_ids:
                visual = visuals.get(vid)
                if visual is None:
                    error("unknown_assessment_visual", aid, "연결한 그림이 없습니다.")
                elif visual.placement.after_id != assessment.item_id or (
                    visual.kind == "screenshot"
                ) != (assessment.selected_kind == "image"):
                    error(
                        "assessment_visual_mismatch",
                        aid,
                        "표현 종류와 그림 배치 대상이 일치해야 합니다.",
                    )
            if assessment.selected_kind == "image" and not assessment.result_ids:
                error("missing_acquisition_result", aid, "이미지의 메인 확보 결과를 연결하세요.")
        elif assessment.selected_kind == "text":
            if assessment.explanation_item_id not in items:
                error(
                    "missing_visual_explanation",
                    aid,
                    "실제 텍스트·코드 설명의 본문 항목을 연결하세요.",
                )
            if assessment.visual_ids:
                error(
                    "unexpected_assessment_visual",
                    aid,
                    "그림을 채택하려면 이미지·도식 표현을 선택하세요.",
                )
        manual.append(
            {
                "code": "visual_assessment_meaning",
                "path": f"{section.kind}.visual_assessments.{aid}",
                "message": "관찰 목표 달성·표현 적합성·대체 이유와 핵심 근거 유지 여부를 검토하세요. 확보 불가 기록만으로 통과시키지 마세요.",
            }
        )
    return {"errors": errors, "manual_review": manual}
