import hashlib
import re
import unicodedata

from .models import AdvancedSection, DraftInput, FoundationSection, Source


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_markdown(text: str) -> str:
    """Ignore layout-only blank lines outside code, retain code indentation/content."""
    lines = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[str] = []
    fence: str | None = None
    for line in lines:
        stripped = line.strip()
        marker = re.match(r"^(\x60{3,}|~{3,})", stripped)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
            result.append(line.rstrip())
        elif fence:
            result.append(line.rstrip())
        elif stripped and stripped != "<empty-block/>":
            result.append(line.rstrip())
    return "\n".join(result)


def render_document(
    draft: DraftInput,
    foundation: FoundationSection,
    advanced: AdvancedSection,
    sources: dict[str, Source],
) -> tuple[str, str]:
    lines: list[str] = []
    used_sources: set[str] = set()

    def cite(section: FoundationSection | AdvancedSection, claim_ids: list[str]) -> str:
        claims = {claim.id: claim for claim in section.claims}
        source_ids = sorted({sid for cid in claim_ids for sid in claims[cid].source_ids})
        used_sources.update(source_ids)
        return " ".join(f"[{sid}]({sources[sid].url})" for sid in source_ids)

    def block(text: str) -> None:
        lines.extend([text, ""])

    block("## 학습 목표·선수지식")
    for objective in foundation.objectives:
        block(f"- {objective}")
    block("**선수지식:** " + (", ".join(foundation.prerequisites) or "별도 선수지식 없음"))
    block("## 핵심 용어")
    for term in foundation.terms:
        block(f"### {term.name}")
        block(f"{term.definition}\n\n**필요한 이유:** {term.why}\n\n**예시:** {term.example}")
        block(cite(foundation, term.claim_ids))
    block("## 기본 원리·쉬운 예시")
    for item in foundation.principles:
        block(f"### {item.heading}")
        block(item.body + "\n\n" + cite(foundation, item.claim_ids))
    for item in foundation.examples:
        label = "가상 설명용 예시" if item.kind == "hypothetical" else "출처로 확인한 예시"
        block(f"### {item.title} ({label})")
        block(item.body + "\n\n" + cite(foundation, item.claim_ids))
    block("## 심화 동작·장단점")
    for item in advanced.concepts:
        block(f"### {item.heading}")
        block(item.body + "\n\n" + cite(advanced, item.claim_ids))
    for item in advanced.tradeoffs:
        block(f"### {item.topic}")
        block(
            f"**선택지:** {item.options}\n\n**장점:** {item.advantages}\n\n"
            f"**한계:** {item.limitations}\n\n**선택 기준:** {item.choose_when}"
        )
        block(cite(advanced, item.claim_ids))
    block("## 실제 사례")
    for case in advanced.cases:
        block(f"### {case.title}")
        for label, value in [
            ("대상 시스템", case.system),
            ("문제 상황", case.problem),
            ("선택한 기술", case.technology),
            ("선택 이유", case.rationale),
            ("확인된 결과", case.outcome),
            ("적용 한계", case.limitations),
        ]:
            block(f"**{label}:** {value}")
        block("**확인된 사실의 근거:** " + cite(advanced, case.claim_ids))
        block("**학습을 위한 해석:** " + case.interpretation)
    block("## 흔한 오해")
    for item in foundation.misconceptions:
        block(f"### {item.heading}")
        block(item.body + "\n\n" + cite(foundation, item.claim_ids))
    block("## 면접 질문·답안")
    for label, section in [("기초", foundation), ("심화", advanced)]:
        for i, question in enumerate(section.questions, 1):
            block(f"### {label} {i}. {question.question}")
            block(f"**모범답안:** {question.answer}\n\n**해설:** {question.explanation}")
            block(cite(section, question.claim_ids))
    block("## 핵심 요약")
    for point in draft.summary:
        section = foundation if point.section == "foundation" else advanced
        block(f"- {point.text} {cite(section, point.claim_ids)}")
    block("## 참고자료")
    for sid in sorted(used_sources):
        source = sources[sid]
        title = source.title.replace("[", r"\[").replace("]", r"\]")
        block(f"- [{sid}: {title}]({source.url}) — 확인일 {source.accessed_at.date().isoformat()}")
    body = "\n".join(lines).strip() + "\n"
    return f"# {draft.title}\n\n{body}", body
