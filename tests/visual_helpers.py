"""Synthetic visual decisions; never live research or user approval."""

from cs_study_mcp.models import VisualAssessment


def add_text_assessments(f, a):
    for section, items in ((f, f.examples), (a, a.cases)):
        section.visual_assessments = [
            VisualAssessment(
                id=f"{section.kind}-assessment-{i}",
                item_id=item.id,
                learning_goal="본문의 모의 동작을 설명할 수 있다.",
                preferred_kind="text",
                rationale="모의 항목은 텍스트로 관찰 목표를 설명할 수 있다.",
                source_ids=["S1"],
                status="resolved",
                selected_kind="text",
                explanation_item_id=item.id,
            )
            for i, item in enumerate(items)
        ]
    return f, a


def review_refs(preview):
    return [
        {"section": item["section"], "assessment_id": item["assessment"]["id"]}
        for item in preview.get("visual_assessments", [])
    ]


def expression_review(preview, role):
    if "korean_expression_targets" not in preview:
        return None
    return {
        "policy_version": 1,
        "focus": "readability" if role == "foundation" else "meaning",
        "reviewed_targets": [item["ref"] for item in preview["korean_expression_targets"]],
    }
