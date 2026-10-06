"""Candidate-bound Korean expression review contracts, without linguistic judgment."""

import re

from .models import OutlineGroup
from .visual_assessment import assessment_policy


def expression_review_policy(profile):
    return profile.get("korean_expression_review_version") == 1


def reviewable_text(text):
    """Protect code and link destinations while retaining authored prose/labels.

    English quotations mixed into prose cannot be inferred here. Reviewers read
    the full candidate and original evidence and preserve quotations themselves.
    """
    text = re.sub(r"<(pre|code)\b[^>]*>.*?</\1\s*>", "\n", text, flags=re.I | re.S)
    lines, fence = [], None
    for line in text.replace("\r\n", "\n").split("\n"):
        # The publication parser accepts fenced code inside nested blockquotes.
        # Strip only the quote prefix for fence detection; keep prose verbatim.
        structural_line = re.sub(r"^(?:\s*> ?)+", "", line)
        match = re.match(r"^\s*(`{3,}|~{3,})(.*)$", structural_line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
            lines.append("")
        elif match:
            fence = match[1]
            lines.append("")
        else:
            lines.append(line)
    text = "\n".join(lines)
    # Matching run lengths preserve backticks that are not code delimiters.
    text = re.sub(r"(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)", "\n", text, flags=re.S)
    text = re.sub(r"!?\[([^\]\n]*)\]\((?:\\.|[^()\\]|\([^()]*\))*\)", r"\1", text)
    text = re.sub(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"')\]]+", "", text)
    text = re.sub(r"</?[A-Za-z][^>]*>", "", text)
    return text.strip()


def target_key(ref):
    if not isinstance(ref, dict):
        ref = ref.model_dump()
    return ref["scope"], ref.get("item_id"), ref["field"]


def expression_targets(plan, foundation, advanced, profile):
    """Enumerate each displayed authored field once, in a deterministic order.

    Array positions are references within this immutable candidate, never IDs
    that can be carried across a reordered candidate. Source titles/evidence,
    diagram definitions and execution/version literals are intentionally absent.
    """
    if not expression_review_policy(profile):
        return []
    targets = []
    sections = {"foundation": foundation, "advanced": advanced}

    def source_ids(section, claim_ids):
        claims = {claim.id: claim for claim in section.claims}
        return sorted({sid for cid in claim_ids if cid in claims for sid in claims[cid].source_ids})

    def add(scope, item_id, field, value, sources=()):
        if not value:
            return
        text = reviewable_text(value)
        if text:
            targets.append({"ref": {"scope": scope, "item_id": item_id, "field": field},
                            "text": text, "source_ids": sorted(set(sources))})

    add("document", None, "title", plan.title)
    for index, point in enumerate(plan.summary):
        add("document", None, f"summary.{index}.text", point.text,
            source_ids(sections[point.section], point.claim_ids))

    def outline(nodes):
        for node in nodes:
            if isinstance(node, OutlineGroup):
                add("document", node.id, "title", node.title)
                outline(node.children)

    outline(plan.outline)
    for field in ("objectives", "prerequisites"):
        for index, value in enumerate(getattr(foundation, field)):
            add("foundation", None, f"{field}.{index}", value)
    fields = {
        "terms": ("name", "definition", "why", "example"),
        "principles": ("heading", "key_point", "body"),
        "examples": ("title", "body"),
        "misconceptions": ("heading", "key_point", "body"),
        "concepts": ("heading", "key_point", "body"),
        "tradeoffs": ("topic", "options", "advantages", "limitations", "choose_when"),
        "cases": ("title", "system", "problem", "technology", "rationale", "outcome", "limitations", "interpretation"),
        "questions": ("question", "answer", "explanation"),
        "visuals": ("title", "caption", "alt_text"),
    }
    for scope, section in sections.items():
        for collection, names in fields.items():
            for item in getattr(section, collection, []):
                sources = source_ids(section, item.claim_ids)
                for field in names:
                    add(scope, item.id, field, getattr(item, field), sources)
                if collection == "visuals" and item.screenshot:
                    # Usage conditions are authored; locator, version, target and
                    # environment strings are original provenance/runtime literals.
                    add(scope, item.id, "screenshot.usage_note", item.screenshot.usage_note,
                        [*sources, item.screenshot.source_id])
        for table in section.comparisons:
            sources = source_ids(section, table.claim_ids)
            add(scope, table.id, "title", table.title, sources)
            for index, value in enumerate(table.columns):
                add(scope, table.id, f"columns.{index}", value, sources)
            for row_index, row in enumerate(table.rows):
                row_sources = source_ids(section, row.claim_ids) or sources
                for cell_index, value in enumerate(row.cells):
                    add(scope, table.id, f"rows.{row_index}.cells.{cell_index}", value, row_sources)
        if assessment_policy(profile):
            for assessment in section.visual_assessments:
                add(scope, assessment.id, "reader_note", assessment.reader_note,
                    [*assessment.source_ids, *source_ids(section, assessment.claim_ids)])
    return targets


def expression_review_issues(review, targets, sources):
    """Validate attested scope and citations, never the translation or its fix."""
    errors = []

    def error(code, path, message):
        errors.append({"code": code, "path": path, "message": message})

    language = review.korean_expression_review
    if language is None:
        error("expression_review_required", "korean_expression_review", "한국어 표현 검수 기록이 필요합니다.")
    else:
        if language.policy_version != 1:
            error("expression_review_policy", "korean_expression_review.policy_version", "작업에 고정된 검수 정책 버전이 필요합니다.")
        focus = "readability" if review.reviewer == "foundation" else "meaning"
        if language.focus != focus:
            error("expression_review_focus", "korean_expression_review.focus", "foundation은 가독성, advanced는 의미 보존 검수를 기록하세요.")
        expected = {target_key(target["ref"]) for target in targets}
        observed = [target_key(ref) for ref in language.reviewed_targets]
        if len(observed) != len(set(observed)) or set(observed) != expected:
            error("expression_review_coverage", "korean_expression_review.reviewed_targets", "같은 후보의 전체 검수 대상 목록을 누락·중복 없이 기록하세요.")
    by_ref = {target_key(target["ref"]): target for target in targets}
    for index, finding in enumerate(review.findings):
        detail = finding.expression_detail
        if detail is None:
            continue
        path = f"findings.{index}.expression_detail"
        target = by_ref.get(target_key(detail.target))
        if target is None:
            error("unknown_expression_target", path, "같은 후보의 실제 검수 대상에 지적을 연결하세요.")
        elif detail.current_text not in target["text"]:
            error("expression_text_mismatch", path, "해당 대상의 실제 검토 가능한 구절을 기록하세요. 코드·URL은 표현 수정 대상이 아닙니다.")
        if detail.problem_kind == "meaning_change" and not detail.evidence_refs:
            error("expression_evidence_required", path, "의미 변화 지적에는 저장된 원문 근거가 필요합니다.")
        if detail.problem_kind == "meaning_change" and finding.severity != "blocking":
            error("expression_meaning_severity", path, "의미가 바뀐 문제는 blocking으로 기록하세요.")
        for ref in detail.evidence_refs:
            source = sources.get(ref.source_id)
            if source is None or ref.evidence_index >= len(source.evidence):
                error("unknown_expression_evidence", path, "존재하는 출처와 원문 발췌 인덱스를 연결하세요.")
    return errors
