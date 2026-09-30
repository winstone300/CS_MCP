"""Structural outline checks. Educational sequencing remains the author's decision."""

from collections import Counter

from .models import OutlineGroup


def primary_items(foundation, advanced):
    return {
        (section.kind, item.id): item
        for section, fields in (
            (foundation, ("terms", "principles", "examples")),
            (advanced, ("concepts", "tradeoffs", "cases")),
        )
        for field in fields
        for item in getattr(section, field)
    }


def outline_issues(plan, foundation, advanced):
    errors, warnings = [], []
    groups, refs = [], []

    def issue(code, path, message):
        errors.append({"code": code, "path": path, "message": message})

    def walk(nodes, depth=0, toggles=0):
        if depth > 12:
            issue("outline_depth", "outline", "목차의 최대 구조 깊이는 12입니다.")
            return
        for node in nodes:
            if isinstance(node, OutlineGroup):
                groups.append(node.id)
                count = toggles + (node.display == "toggle")
                if count > 2:
                    warnings.append(
                        {
                            "code": "deep_toggle",
                            "path": node.id,
                            "message": "토글 중첩은 2단계를 권장합니다.",
                        }
                    )
                walk(node.children, depth + 1, count)
            else:
                refs.append((node.section, node.item_id))

    walk(plan.outline)
    for key, count in Counter(groups).items():
        if count > 1:
            issue("duplicate_group", key, "목차 그룹 ID는 고유해야 합니다.")
    expected = set(primary_items(foundation, advanced))
    for ref, count in Counter(refs).items():
        if ref not in expected:
            issue("unknown_outline_item", ".".join(ref), "직접 배치 가능한 본문 항목이 아닙니다.")
        if count > 1:
            issue("duplicate_outline_item", ".".join(ref), "본문은 한 번만 배치하세요.")
    for ref in sorted(expected - set(refs), key=str):
        issue("missing_outline_item", str(ref), "목차에서 본문 항목이 누락되었습니다.")
    return {"errors": errors, "warnings": warnings}
