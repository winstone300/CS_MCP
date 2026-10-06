"""Frozen presentation rules and structural checks; no semantic/AI judgments."""

import re
from collections import Counter
from copy import deepcopy

from .models import AdvancedSection, Explanation, FoundationSection, Source
from .visual_assessment import assessment_issues, assessment_policy


def profile_snapshot(name: str = "study_readable_v2") -> dict:
    """Return an independent snapshot to persist when a study is created."""
    if name == "study_topic_v3":
        profile = profile_snapshot("study_readable_v2")
        profile.update(name=name, version=3, schema_version=3, renderer_version=3,
                       verification_version=3)
        profile["guidance"].update(
            order=["핵심 요약·대표 그림", "목표·선수지식", "주제별 중첩 토글",
                   "전체 연결 시나리오·실제 사례", "면접 키워드·질문", "참고자료"],
            composition="작성 역할과 읽는 순서를 분리합니다. 기존 항목을 역할과 ID로 정확히 한 번 배치합니다.",
            review="같은 버전의 Markdown 전체 본문·중첩 토글·코드·표·도식·캡션과 연결된 그림을 양쪽 역할이 확인합니다.",
        )
        profile["roles"]["main"] = "목차·요약을 구성하고 prepare_document_preview로 교차 검토할 불변 후보를 준비합니다. 검토한 후보만 save_draft로 승격합니다."
        profile["roles"]["advanced"] += " 전체 개념 연결 시나리오도 근거와 함께 작성합니다."
        profile["recommendations"]["toggle_depth"] = 2
        profile["visual_assessment_policy_version"] = 1
        profile["guidance"]["visual_assessments"] = (
            "foundation.examples와 advanced.cases는 항목마다 표현 판단을 기록합니다. "
            "이미지·도식·텍스트 선택과 메인의 확보 결과를 구분하고 같은 후보에서 대체 설명의 적합성까지 검토합니다."
        )
        profile["visual_assessment_required_targets"] = {"foundation": ["examples"], "advanced": ["cases"]}
        profile["roles"]["main"] += " 실제 확보 결과를 record_visual_acquisition으로 즉시 저장하고 작성 역할에 반영을 요청합니다."
        profile["roles"]["foundation"] += " 모든 examples 항목의 표현 판단과 확보 결과 연결을 저장합니다."
        profile["roles"]["advanced"] += " 모든 cases 항목의 표현 판단과 확보 결과 연결을 저장합니다."
        profile["korean_expression_review_version"] = 1
        profile["korean_expression_review_focus"] = {"foundation": "readability", "advanced": "meaning"}
        profile["guidance"]["korean_expression_review"] = (
            "동일 후보의 제목·요약·목차·본문·표·캡션·질문·답안을 모두 검수합니다. "
            "foundation은 한국어 가독성·용어 설명·표기 일관성, advanced는 원문 의미·조건·가능성·예외를 확인합니다. "
            "의미 변화·개념 이해를 방해하는 문제는 blocking, 문체 개선은 advisory입니다. "
            "원문·코드·URL·제품명은 보존하고 Python은 검토 기록의 범위·참조만 검사합니다."
        )
        profile["roles"]["foundation"] += " 전체 후보의 한국어 가독성 검수와 대상 목록을 교차 검토에 기록합니다."
        profile["roles"]["advanced"] += " 전체 후보의 원문 의미 보존 검수와 대상 목록을 교차 검토에 기록합니다."
        profile["roles"]["research"] += " 중요한 영어 표현의 원문·문맥·위치를 보존합니다."
        profile["roles"]["main"] += " 용어 표기를 통일하고 표현 수정은 담당 작성자에 배정합니다."
        profile["roles"]["notion_writer"] += " 승인된 한국어 표현을 그대로 발행·재조회합니다."
        return profile
    if name not in {"legacy_v1", "study_readable_v2"}:
        raise ValueError(f"알 수 없는 문서 프로필: {name}")
    version = 1 if name == "legacy_v1" else 2
    if name == "legacy_v1":
        return {
            "name": name,
            "version": version,
            "schema_version": version,
            "renderer_version": version,
            "verification_version": version,
            "guidance": {
                "order": [
                    "학습 목표·선수지식",
                    "핵심 용어",
                    "기본 원리·쉬운 예시",
                    "심화 동작·장단점",
                    "실제 사례",
                    "흔한 오해",
                    "면접 질문·답안",
                    "핵심 요약",
                    "참고자료",
                ],
                "explanation": "작업에 고정된 규칙과 기존 문서 필드를 따릅니다.",
                "terms": "정의·필요한 이유·예시를 모두 작성합니다.",
                "citations": "기존 출처 ID 링크와 Markdown 검증 방식을 유지합니다.",
                "compatibility": "기존 본문·해시·승인을 보존하며 새 형식으로 자동 전환하지 않습니다.",
            },
            "roles": {},
            "recommendations": {},
        }
    return {
        "name": name,
        "version": version,
        "schema_version": version,
        "renderer_version": version,
        "verification_version": version,
        "diagram_transport": "mermaid",
        "citation_style": "references_only",
        "review_format": "markdown",
        "toggle_format": "details",
        "capability_verified": False,
        "guidance": {
            "order": [
                "핵심 요약",
                "대표 그림",
                "학습 목표·선수지식·용어",
                "기초",
                "심화",
                "실제 사례",
                "면접 질문",
                "참고자료",
            ],
            "explanation": "핵심 문장 → 이유 → 예시. 한 문단에 한 개념을 설명합니다.",
            "terms": "짧은 정의를 쓰고 긴 필요성·예시는 관련 본문으로 옮깁니다.",
            "visuals": "이미지가 이해에 도움이 되면 관련 본문에 실제 이미지를 첨부합니다. 공식 공개 이미지를 우선하고, 적절한 이미지가 없으면 메인이 공개 웹·재현 가능한 실습 화면을 직접 캡처해 프로젝트 내부에 저장하고 register_visual_asset으로 등록합니다. 캡처 대상·시각·환경·설명·근거·이용 조건을 기록하며 확보하지 못하면 생략 이유와 텍스트·도식을 제공합니다.",
            "placement": "overview 또는 자기 역할의 안정적인 본문 항목 ID 뒤에 배치합니다.",
            "misconceptions": "related_item_id로 관련 설명 바로 뒤에 배치합니다.",
            "citations": "본문·요약·표·답안·그림 캡션에는 출처를 표시하지 않습니다. claim_ids/source_ids는 검증용으로 유지하고 참고 사이트·문헌과 이미지 출처·캡처 상세는 마지막 참고 문헌에 표시합니다.",
            "review": "Markdown의 본문·표·도식·캡션과 연결된 그림을 원문과 검토합니다. HTML 및 브라우저 화면 검증은 요구하지 않습니다.",
            "drafts": "초기 미완성 초안은 저장할 수 있습니다. 오류는 최종 통합 전에 해결합니다.",
        },
        "roles": {
            "main": "검증된 요약 3~5개를 작성하고 전체 흐름·중복·승인 묶음을 확인합니다.",
            "research": "도식의 근거와 공개 이미지의 원문 위치·URL·이용 조건을 수집합니다. 직접 캡처할 대상·재현 절차·환경은 메인에게 전달합니다.",
            "foundation": "짧은 용어 정의·대표 구조도·기초 비교표·쉬운 예시를 작성합니다.",
            "advanced": "내부 동작·실행 순서·선택 기준과 실제 사례를 작성합니다.",
            "notion_writer": "승인된 배치·표·도식·접힌 답안을 보존하고 실제 재조회한 본문·구조·자산을 기록합니다. Notion 화면 확인은 완료 조건이 아닙니다.",
        },
        "recommendations": {
            "summary_min": 3,
            "summary_max": 5,
            "paragraph_characters": 400,
            "table_columns": 5,
            "table_rows": 10,
            "overview_visuals": 1,
        },
    }


def document_blueprint(profile: dict, role: str) -> dict:
    """Describe the persisted profile, never look up a mutable current default."""
    if role not in {"main", "research", "foundation", "advanced", "notion_writer"}:
        raise ValueError(f"알 수 없는 역할: {role}")
    return {
        "role": role,
        "profile": deepcopy(profile),
        "responsibility": profile.get("roles", {}).get(role, "기존 작업의 규칙을 따릅니다."),
        "instructions": "구성표만 반환합니다. 의미 판단·요약·그림 정의·질문 작성은 Codex가 수행합니다.",
    }


def body_items(section: FoundationSection | AdvancedSection) -> list:
    if isinstance(section, FoundationSection):
        return [*section.terms, *section.principles, *section.examples, *section.misconceptions]
    return [*section.concepts, *section.tradeoffs, *section.cases]


def presentation_issues(
    foundation: FoundationSection | None,
    advanced: AdvancedSection | None,
    sources: dict[str, Source],
    profile: dict,
) -> dict:
    """Check IDs, references, placement and readability thresholds, not correctness."""
    result: dict[str, list[dict]] = {"errors": [], "warnings": [], "manual_review": []}
    if profile.get("name") == "legacy_v1":
        return result
    recommendations = profile.get("recommendations", {})

    def add(level: str, code: str, path: str, message: str) -> None:
        result[level].append({"code": code, "path": path, "message": message})

    overview_count = 0
    visual_count = 0
    for section in (foundation, advanced):
        if section is None:
            continue
        if assessment_policy(profile):
            assessment_report = assessment_issues(section, sources)
            result["errors"].extend(assessment_report["errors"])
            result["manual_review"].extend(assessment_report["manual_review"])
        items = body_items(section)
        claims = {claim.id: claim for claim in section.claims}
        item_ids = {item.id for item in items if item.id}
        all_items = [*items, *section.comparisons, *section.visuals, *section.questions]
        id_counts = Counter(item.id for item in all_items if item.id)
        for identifier, count in id_counts.items():
            if count > 1:
                add(
                    "errors",
                    "duplicate_item_id",
                    f"{section.kind}.{identifier}",
                    "역할 내 항목 ID가 중복됩니다.",
                )
        paragraphs: Counter[str] = Counter()
        for index, item in enumerate(items):
            path = f"{section.kind}.{item.id or index}"
            if not item.id:
                add(
                    "errors",
                    "missing_item_id",
                    path,
                    "새 문서에는 안정적인 본문 항목 ID가 필요합니다.",
                )
            if isinstance(item, Explanation) and not item.key_point:
                add(
                    "errors",
                    "missing_key_point",
                    path,
                    "작성자가 짧은 핵심 문장을 제공해야 합니다.",
                )
            for name, value in item.model_dump().items():
                if not isinstance(value, str) or name in {"id", "related_item_id"}:
                    continue
                for paragraph in re.split(r"\n\s*\n", value):
                    normalized = " ".join(paragraph.split())
                    if len(normalized) > recommendations.get("paragraph_characters", 400):
                        add(
                            "warnings",
                            "long_paragraph",
                            f"{path}.{name}",
                            "긴 문단을 개념별로 나누는 것을 권장합니다.",
                        )
                    if len(normalized) > 50:
                        paragraphs[normalized] += 1
        if any(count > 1 for count in paragraphs.values()):
            add(
                "warnings",
                "repeated_paragraph",
                section.kind,
                "동일한 긴 문단이 반복됩니다. 작성자가 중복을 검토하세요.",
            )
        if isinstance(section, FoundationSection):
            primary_ids = {
                item.id
                for item in [*section.terms, *section.principles, *section.examples]
                if item.id
            }
            for misconception in section.misconceptions:
                if misconception.related_item_id not in primary_ids:
                    add(
                        "errors",
                        "invalid_misconception_target",
                        f"foundation.{misconception.id}",
                        "오해를 같은 역할의 용어·원리·예시 ID에 연결하세요.",
                    )

        def check_claims(
            ids: list[str], path: str, *, required: bool = True, claim_map=claims
        ) -> None:
            if required and not ids:
                add(
                    "errors", "missing_claim_reference", path, "표·그림을 검증된 주장과 연결하세요."
                )
            for cid in ids:
                if cid not in claim_map:
                    add("errors", "unknown_claim", path, f"등록되지 않은 주장: {cid}")
                else:
                    for sid in claim_map[cid].source_ids:
                        if sid not in sources:
                            add("errors", "unknown_source", path, f"등록되지 않은 출처: {sid}")

        for material in [*section.comparisons, *section.visuals]:
            path = f"{section.kind}.{material.id}"
            if material.placement.section != section.kind:
                add("errors", "foreign_placement", path, "자기 역할의 본문에만 배치할 수 있습니다.")
            if (
                material.placement.after_id is not None
                and material.placement.after_id not in item_ids
            ):
                add("errors", "unknown_placement", path, "배치 대상 본문 항목 ID가 없습니다.")
        for table in section.comparisons:
            path = f"{section.kind}.{table.id}"
            check_claims(table.claim_ids, path, required=False)
            if not table.rows:
                add("errors", "empty_table", path, "비교표에는 최소 한 행이 필요합니다.")
            for index, row in enumerate(table.rows):
                if len(row.cells) != len(table.columns):
                    add(
                        "errors",
                        "table_width",
                        f"{path}.rows.{index}",
                        "행의 셀 수가 열 수와 다릅니다.",
                    )
                check_claims(row.claim_ids, f"{path}.rows.{index}", required=not table.claim_ids)
            if len(table.columns) > recommendations.get("table_columns", 5) or len(
                table.rows
            ) > recommendations.get("table_rows", 10):
                add(
                    "warnings",
                    "large_table",
                    path,
                    "좁은 화면에서 읽기 쉽게 표를 나누는 것을 권장합니다.",
                )
            add(
                "manual_review",
                "table_meaning",
                path,
                "비교 조건·열 제목·행별 근거가 공정하고 정확한지 원문으로 검토하세요.",
            )
        for visual in section.visuals:
            visual_count += 1
            overview_count += visual.placement.after_id is None
            path = f"{section.kind}.{visual.id}"
            check_claims(visual.claim_ids, path)
            if visual.asset_hash and not re.fullmatch(r"[0-9a-f]{64}", visual.asset_hash):
                add(
                    "errors", "invalid_asset_hash", path, "자산 해시는 소문자 SHA-256이어야 합니다."
                )
            if visual.kind == "screenshot":
                if visual.screenshot is None or not visual.asset_hash:
                    add(
                        "errors",
                        "missing_screenshot_asset",
                        path,
                        "스크린샷의 원문 정보와 불변 자산 해시가 필요합니다.",
                    )
                if visual.diagram_spec is not None:
                    add(
                        "errors",
                        "unexpected_diagram_spec",
                        path,
                        "스크린샷에 도식 정의를 함께 넣을 수 없습니다.",
                    )
                if visual.screenshot is not None and visual.screenshot.source_id not in sources:
                    add(
                        "errors",
                        "unknown_screenshot_source",
                        path,
                        "스크린샷 원문 출처가 등록되지 않았습니다.",
                    )
            elif not visual.diagram_spec or visual.screenshot is not None:
                add(
                    "errors",
                    "invalid_diagram",
                    path,
                    "도식은 Mermaid 정의를 제공하고 스크린샷 메타데이터는 제외하세요.",
                )
            add(
                "manual_review",
                "visual_meaning",
                path,
                "실제 그림과 원문을 함께 보고 화살표·경계·생략·시간 순서·캡션·이용 조건을 검토하세요.",
            )
    if visual_count == 0:
        add(
            "warnings",
            "no_visuals",
            "visuals",
            "구조·흐름의 이해를 돕는 그림이 적합한지 검토하세요. 그림 수는 강제하지 않습니다.",
        )
    if overview_count > recommendations.get("overview_visuals", 1):
        add(
            "warnings",
            "crowded_overview",
            "visuals",
            "첫 화면은 대표 그림 하나를 우선하고 나머지는 관련 본문에 배치하세요.",
        )
    return result
