from copy import deepcopy

import pytest
from conftest import draft_input, sample_sections, sample_source

from cs_study_mcp.models import ComparisonTable, Placement, ScreenshotSource, TableRow, Visual
from cs_study_mcp.presentation import (
    body_items,
    document_blueprint,
    presentation_issues,
    profile_snapshot,
)
from cs_study_mcp.render import render_document
from cs_study_mcp.render_v2 import render_readable


def readable_sections():
    foundation, advanced = sample_sections()
    for section in (foundation, advanced):
        for index, item in enumerate(body_items(section)):
            item.id = f"{section.kind}_{index}"
            if hasattr(item, "key_point"):
                item.key_point = f"{item.heading} 핵심 문장"
    foundation.misconceptions[0].related_item_id = foundation.principles[0].id
    return foundation, advanced


def diagram(*, after_id=None, spec="flowchart LR\n  A[프로세스] --> B[스레드]"):
    return Visual(
        id="overview",
        kind="structure",
        title="주소 공간 구조",
        placement=Placement(section="foundation", after_id=after_id),
        caption="스레드의 공유 경계를 보여 주는 모의 그림입니다.",
        alt_text="프로세스에서 스레드로 연결된 화살표",
        claim_ids=["F1"],
        diagram_spec=spec,
    )


def test_profile_snapshots_are_independent_and_blueprint_uses_persisted_rules():
    profile = profile_snapshot()
    profile["guidance"]["explanation"] = "고정된 과거 지침"
    blueprint = document_blueprint(profile, "foundation")
    assert blueprint["profile"]["guidance"]["explanation"] == "고정된 과거 지침"
    blueprint["profile"]["guidance"]["explanation"] = "외부 변경"
    assert profile["guidance"]["explanation"] == "고정된 과거 지침"
    assert profile_snapshot()["guidance"]["explanation"] != "고정된 과거 지침"
    with pytest.raises(ValueError):
        profile_snapshot("unknown")


def test_legacy_models_remain_readable_and_legacy_renderer_is_unchanged():
    foundation, advanced = sample_sections()
    before = render_document(draft_input(), foundation, advanced, {"S1": sample_source()})
    foundation, advanced = readable_sections()
    foundation.visuals = [diagram()]
    after = render_document(draft_input(), foundation, advanced, {"S1": sample_source()})
    assert before == after
    assert presentation_issues(foundation, advanced, {}, profile_snapshot("legacy_v1")) == {
        "errors": [],
        "warnings": [],
        "manual_review": [],
    }


def test_readable_validation_requires_ids_key_points_and_linked_misconceptions():
    foundation, advanced = sample_sections()
    result = presentation_issues(foundation, advanced, {"S1": sample_source()}, profile_snapshot())
    codes = {issue["code"] for issue in result["errors"]}
    assert {"missing_item_id", "missing_key_point", "invalid_misconception_target"} <= codes


def test_structural_errors_are_separate_from_readability_warnings():
    foundation, advanced = readable_sections()
    foundation.principles[0].body = "긴 문단입니다. " * 100
    result = presentation_issues(foundation, advanced, {"S1": sample_source()}, profile_snapshot())
    assert result["errors"] == []
    assert "long_paragraph" in {issue["code"] for issue in result["warnings"]}
    foundation.visuals = [diagram(after_id="absent")]
    foundation.visuals[0].claim_ids = ["absent"]
    foundation.visuals[0].placement.section = "advanced"
    result = presentation_issues(foundation, advanced, {"S1": sample_source()}, profile_snapshot())
    assert {"unknown_placement", "unknown_claim", "foreign_placement"} <= {
        issue["code"] for issue in result["errors"]
    }


def test_duplicate_ids_and_table_row_evidence_and_width_are_checked():
    foundation, advanced = readable_sections()
    foundation.comparisons = [
        ComparisonTable(
            id=foundation.terms[0].id,
            title="비교표",
            columns=["A", "B"],
            rows=[TableRow(cells=["한 셀"])],
            placement=Placement(section="foundation", after_id=foundation.terms[0].id),
        )
    ]
    result = presentation_issues(foundation, advanced, {"S1": sample_source()}, profile_snapshot())
    assert {"duplicate_item_id", "table_width", "missing_claim_reference"} <= {
        issue["code"] for issue in result["errors"]
    }


def test_summary_and_overview_precede_body_and_numeric_citations_replace_ids(tmp_path):
    foundation, advanced = readable_sections()
    foundation.visuals = [diagram()]
    asset = tmp_path / "diagram.svg"
    asset.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    rendered = render_readable(
        draft_input(),
        foundation,
        advanced,
        {"S1": sample_source()},
        profile_snapshot(),
        [
            {
                "section": "foundation",
                "visual_id": "overview",
                "hash": "a" * 64,
                "path": str(asset),
            },
        ],
    )
    markdown = rendered["markdown"]
    assert (
        markdown.index("## 핵심 요약")
        < markdown.index("### 주소 공간 구조")
        < markdown.index("## 학습 목표")
    )
    assert "[1](https://example.org/test-fixture)" in markdown
    assert "[S1]" not in markdown
    assert markdown.count("[1: 모의 원문]") == 1
    assert "모의 필요성" in markdown and "간단한 예시" in markdown
    assert asset.as_uri() in rendered["html"]
    assert f'alt="{foundation.visuals[0].alt_text}"' in rendered["html"]
    assert "<details>" in rendered["html"]
    assert rendered == render_readable(
        draft_input(),
        foundation,
        advanced,
        {"S1": sample_source()},
        profile_snapshot(),
        [
            {
                "section": "foundation",
                "visual_id": "overview",
                "hash": "a" * 64,
                "path": str(asset),
            },
        ],
    )


def test_misconceptions_and_visuals_follow_the_stable_target_after_reordering():
    foundation, advanced = readable_sections()
    original = foundation.principles[0]
    added = original.model_copy(
        update={"id": "extra", "heading": "두 번째 원리", "body": "두 번째 원문"}
    )
    foundation.principles = [added, original]
    foundation.visuals = [diagram(after_id=original.id)]
    rendered = render_readable(
        draft_input(), foundation, advanced, {"S1": sample_source()}, profile_snapshot(), []
    )
    text = rendered["markdown"]
    assert (
        text.index("### 두 번째 원리")
        < text.index("### 기본 원리")
        < text.index("#### 흔한 오해")
        < text.index("### 주소 공간 구조")
        < text.index("### 설명용 상황")
    )


def test_tables_escape_separators_and_preserve_newlines_and_row_citations():
    foundation, advanced = readable_sections()
    foundation.comparisons = [
        ComparisonTable(
            id="comparison",
            title="선택 비교",
            columns=["선택지", "조건"],
            rows=[TableRow(cells=["A | B", "첫 줄\n다음 줄"], claim_ids=["F1"])],
            placement=Placement(section="foundation", after_id=foundation.principles[0].id),
        )
    ]
    rendered = render_readable(
        draft_input(), foundation, advanced, {"S1": sample_source()}, profile_snapshot(), []
    )
    table = next(block for block in rendered["blocks"] if block["type"] == "table")
    assert len(table["rows"][0]) == 2
    assert r"A \| B" == table["rows"][0][0]
    assert "A | B" in rendered["html"]
    assert "첫 줄" in table["rows"][0][1] and "다음 줄" in table["rows"][0][1]
    assert "[1]" in table["rows"][0][1]
    assert '<table header-row="true">' in rendered["notion_markdown"]
    assert "\n\t<tr>\n\t\t<td>선택지</td>" in rendered["notion_markdown"]
    assert "| 선택지 | 조건 |" in rendered["markdown"]
    assert "<table" not in rendered["markdown"]


def test_notion_table_preserves_cells_with_entities_and_header_semantics():
    from cs_study_mcp.publication import canonicalize_markdown

    foundation, advanced = readable_sections()
    foundation.comparisons = [
        ComparisonTable(
            id="symbols",
            title="특수문자 표",
            columns=["A & B", "조건"],
            rows=[TableRow(cells=["x < y > z", "두 줄\n예시"], claim_ids=["F1"])],
            placement=Placement(section="foundation", after_id=foundation.principles[0].id),
        )
    ]
    rendered = render_readable(
        draft_input(), foundation, advanced, {"S1": sample_source()}, profile_snapshot(), []
    )
    expected = next(
        block for block in canonicalize_markdown(rendered["markdown"]) if block["type"] == "table"
    )
    actual = next(block for block in rendered["blocks"] if block["type"] == "table")
    assert actual == expected
    assert actual["header_row"] is True
    assert actual["header_column"] is False
    assert "A &amp; B" in rendered["notion_markdown"]
    assert "x &lt; y &gt; z" in rendered["notion_markdown"]


def test_questions_have_separate_markdown_answers_and_preserved_notion_toggles():
    foundation, advanced = readable_sections()
    rendered = render_readable(
        draft_input(), foundation, advanced, {"S1": sample_source()}, profile_snapshot(), []
    )
    assert rendered["markdown"].index("심화 질문 3?") < rendered["markdown"].index(
        "## 면접 답안·해설"
    )
    toggles = [block for block in rendered["blocks"] if block["type"] == "toggle"]
    assert len(toggles) == 6
    assert all(
        any("모범답안" in child.get("text", "") for child in toggle["children"])
        for toggle in toggles
    )
    assert rendered["notion_markdown"].count("<summary>답안·해설</summary>") == 6
    assert rendered["notion_markdown"].count("\n\t**모범답안:**") == 6


def test_image_transport_uses_approved_hash_and_retains_caption(tmp_path):
    foundation, advanced = readable_sections()
    foundation.visuals = [diagram()]
    profile = deepcopy(profile_snapshot())
    profile["diagram_transport"] = "image"
    with pytest.raises(ValueError, match="승인용 자산"):
        render_readable(draft_input(), foundation, advanced, {"S1": sample_source()}, profile, [])
    asset = tmp_path / "diagram.png"
    asset.write_bytes(b"fixture")
    rendered = render_readable(
        draft_input(),
        foundation,
        advanced,
        {"S1": sample_source()},
        profile,
        [
            {
                "section": "foundation",
                "visual_id": "overview",
                "hash": "a" * 64,
                "path": str(asset),
            },
        ],
    )
    image = next(block for block in rendered["blocks"] if block["type"] == "image")
    assert image["ref"] == "asset://" + "a" * 64
    assert foundation.visuals[0].caption in rendered["markdown"]
    assert foundation.visuals[0].alt_text in rendered["html"]


def test_preview_keeps_diagram_labels_readable_with_keyboard_scroll_and_original_link(tmp_path):
    foundation, advanced = readable_sections()
    foundation.visuals = [diagram()]
    asset = tmp_path / "diagram.png"
    asset.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (1232).to_bytes(4, "big") + (800).to_bytes(4, "big"))
    rendered = render_readable(
        draft_input(), foundation, advanced, {"S1": sample_source()}, profile_snapshot(),
        [{"section": "foundation", "visual_id": "overview", "hash": "a" * 64, "path": str(asset)}],
    )
    html = rendered["html"]
    assert 'style="width:616px"' in html
    assert 'class="visual-scroll" tabindex="0" role="region"' in html
    assert "overflow-x:auto" in html and "max-width:none;height:auto" in html
    assert f'href="{asset.as_uri()}" target="_blank" rel="noopener noreferrer"' in html
    assert "이미지 크게 보기" in html and f'alt="{foundation.visuals[0].alt_text}"' in html
    assert "이미지 크게 보기" not in rendered["notion_markdown"]
    assert "visual-scroll" not in str(rendered["blocks"])


def test_html_preview_rejects_unsupported_authored_html():
    from cs_study_mcp.publication import PublicationFormatError

    foundation, advanced = readable_sections()
    foundation.principles[0].body = '문장 안 <script>alert("x")</script> 원문'
    with pytest.raises(PublicationFormatError):
        render_readable(
            draft_input(), foundation, advanced, {"S1": sample_source()}, profile_snapshot(), []
        )


def test_screenshot_requires_provenance_and_preserves_version_and_usage_note(tmp_path):
    foundation, advanced = readable_sections()
    screenshot = diagram().model_copy(update={"kind": "screenshot", "diagram_spec": None})
    foundation.visuals = [screenshot]
    result = presentation_issues(foundation, advanced, {"S1": sample_source()}, profile_snapshot())
    assert "missing_screenshot_asset" in {issue["code"] for issue in result["errors"]}
    screenshot.asset_hash = "b" * 64
    screenshot.screenshot = ScreenshotSource(
        source_id="S1",
        image_url="https://example.org/screenshot.png",
        locator="공식 문서의 모의 그림 1",
        accessed_at=sample_source().accessed_at,
        product_version="1.2",
        usage_note="테스트용 이용 조건",
    )
    path = tmp_path / "screenshot.png"
    path.write_bytes(b"fixture")
    result = presentation_issues(foundation, advanced, {"S1": sample_source()}, profile_snapshot())
    assert result["errors"] == []
    rendered = render_readable(
        draft_input(),
        foundation,
        advanced,
        {"S1": sample_source()},
        profile_snapshot(),
        [
            {
                "section": "foundation",
                "visual_id": screenshot.id,
                "hash": screenshot.asset_hash,
                "path": str(path),
                "remote_url": str(screenshot.screenshot.image_url),
            }
        ],
    )
    assert "제품 버전 1.2" in rendered["notion_markdown"]
    assert "테스트용 이용 조건" in rendered["notion_markdown"]
    assert "공식 문서의 모의 그림 1" in rendered["notion_markdown"]
    image = next(block for block in rendered["blocks"] if block["type"] == "image")
    assert image["ref"] == str(screenshot.screenshot.image_url)
    assert f"asset://{screenshot.asset_hash}" in rendered["markdown"]
    assert path.as_uri() in rendered["html"]
    assert f'src="{screenshot.screenshot.image_url}"' not in rendered["html"]
