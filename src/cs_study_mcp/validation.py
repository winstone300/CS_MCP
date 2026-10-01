from collections import Counter

from .models import AdvancedSection, FoundationSection, SectionKind, Source


def question_blueprint(role: SectionKind, total: int = 6) -> dict:
    if not 2 <= total <= 20:
        raise ValueError("질문 수는 전체 2~20개여야 합니다.")
    count = (total + 1) // 2 if role == "foundation" else total // 2
    if role == "foundation":
        kinds = ["concept"] * min(2, count) + ["comparison"] * max(0, count - 2)
    else:
        kinds = ["comparison"] + ["application"] * (count - 1)
    return {
        "role": role,
        "count": count,
        "types": dict(Counter(kinds)),
        "required_fields": ["id", "kind", "question", "answer", "explanation", "claim_ids"],
        "instructions": "Codex가 질문과 답안을 생성합니다. 이 도구는 배분표만 제공합니다.",
    }


def section_issues(
    section: FoundationSection | AdvancedSection,
    sources: dict[str, Source],
    question_count: int,
    current_foundation_version: int,
) -> list[dict]:
    issues: list[dict] = []

    def add(code: str, path: str, message: str) -> None:
        issues.append({"code": code, "path": f"{section.kind}.{path}", "message": message})

    claims = {claim.id: claim for claim in section.claims}
    if len(claims) != len(section.claims):
        add("duplicate_claim", "claims", "주장 ID가 중복됩니다.")
    if not claims:
        add("missing_claims", "claims", "근거와 연결된 주장이 필요합니다.")
    for claim in section.claims:
        if claim.verdict != "supported":
            add(
                "unresolved_claim", f"claims.{claim.id}", "불확실하거나 상충하는 주장을 해결하세요."
            )
        if not claim.source_ids:
            add("missing_evidence", f"claims.{claim.id}", "주장의 원문 출처가 없습니다.")
        for source_id in claim.source_ids:
            if source_id not in sources:
                add("unknown_source", f"claims.{claim.id}", f"등록되지 않은 출처: {source_id}")

    def references(items: list, path: str, *, optional_hypothetical: bool = False) -> None:
        for i, item in enumerate(items):
            optional = optional_hypothetical and item.kind == "hypothetical"
            if not item.claim_ids and not optional:
                add("missing_claim_reference", f"{path}.{i}", "설명과 검증된 주장을 연결하세요.")
            for claim_id in item.claim_ids:
                if claim_id not in claims:
                    add("unknown_claim", f"{path}.{i}", f"등록되지 않은 주장: {claim_id}")

    if isinstance(section, FoundationSection):
        for field in ("objectives", "terms", "principles", "examples", "misconceptions"):
            if not getattr(section, field):
                add("missing_section", field, f"필수 기초 항목이 비어 있습니다: {field}")
        for field in ("terms", "principles", "misconceptions"):
            references(getattr(section, field), field)
        references(section.examples, "examples", optional_hypothetical=True)
    else:
        if section.foundation_version != current_foundation_version:
            add(
                "stale_foundation",
                "foundation_version",
                "현재 기초 버전을 반영해 심화 내용을 갱신하세요.",
            )
        for field in ("concepts", "tradeoffs", "cases"):
            if not getattr(section, field):
                code = "missing_real_case" if field == "cases" else "missing_section"
                add(code, field, f"필수 심화 항목이 비어 있습니다: {field}")
            references(getattr(section, field), field)
        for i, case in enumerate(section.cases):
            case_sources = {
                sid for cid in case.claim_ids if cid in claims for sid in claims[cid].source_ids
            }
            if not case_sources or any(sid not in sources for sid in case_sources):
                add("missing_case_evidence", f"cases.{i}", "실제 사례의 원문 근거가 필요합니다.")

    references(section.questions, "questions")
    expected = question_blueprint(section.kind, question_count)["types"]
    if dict(Counter(q.kind for q in section.questions)) != expected:
        add("question_distribution", "questions", f"필요한 질문 배분: {expected}")
    if len({q.id for q in section.questions}) != len(section.questions):
        add("duplicate_question_id", "questions", "질문 ID가 중복됩니다.")
    normalized = [" ".join(q.question.casefold().split()) for q in section.questions]
    if len(set(normalized)) != len(normalized):
        add("duplicate_question", "questions", "동일한 질문이 중복됩니다.")
    return issues


def combined_question_issues(
    foundation: FoundationSection, advanced: AdvancedSection
) -> list[dict]:
    foundation_questions = {" ".join(q.question.casefold().split()) for q in foundation.questions}
    duplicates = [
        q.id
        for q in advanced.questions
        if " ".join(q.question.casefold().split()) in foundation_questions
    ]
    if duplicates:
        return [
            {
                "code": "duplicate_question",
                "path": "advanced.questions",
                "message": f"기초 질문과 중복: {duplicates}",
            }
        ]
    return []
