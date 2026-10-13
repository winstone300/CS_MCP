"""Synthetic layout/compatibility contracts, not a semantic CS quality assessment."""

import hashlib
from pathlib import Path

import pytest
from conftest import draft_input, sample_source
from test_presentation_workflow import approve_v2
from test_presentation_workflow import readable_sections as baseline_sections
from test_topic_render import flattened
from test_topic_workflow import make_plan, promote, readable_sections, review

from cs_study_mcp.models import OutlineGroup, OutlineItemRef, Placement, ScreenshotSource, Visual
from cs_study_mcp.presentation import profile_snapshot
from cs_study_mcp.publication import canonicalize_markdown, verify_readback
from cs_study_mcp.render import digest, render_document
from cs_study_mcp.render_v2 import render_readable
from cs_study_mcp.render_v3 import render_topic
from cs_study_mcp.service import StudyService
from cs_study_mcp.storage import dumps
from cs_study_mcp.topic import primary_items


def prior_grouping_profile():
    profile = profile_snapshot("study_topic_v3")
    profile.pop("topic_grouping_policy_version")
    profile["guidance"].pop("topic_groups")
    profile["roles"]["main"] = profile["roles"]["main"].replace(
        " 주요 주제는 toggle 그룹으로 묶고, 세부 구현·예외는 두 번째 단계 toggle로 배치합니다.",
        " 핵심 흐름은 펼친 heading, 세부 구현·예외는 관련 심화 toggle로 배치합니다.",
    )
    return profile


def prior_topic_profile():
    profile = prior_grouping_profile()
    profile.pop("content_unit_layout_version")
    profile["guidance"].pop("content_units")
    profile["guidance"]["explanation"] = "핵심 문장 → 이유 → 예시. 한 문단에 한 개념을 설명합니다."
    return profile


@pytest.mark.parametrize(("name", "expected"), [
    ("legacy_v1", "cbb89ff5497e0a3b7aa69cb6c74e97f41991ab9d95da42ba7fb9ac470353ae79"),
    ("study_readable_v2", "28d786311c1d19c7359505b6bd9e574e728550d092bfa1f161561bbbd3d68730"),
    ("study_topic_v3", "9c9b06a0828bcf0368868e01f3812d0304a507c1a67e213ccf6ab6b01e29b8fe"),
])
def test_prior_profiles_keep_pre_content_unit_rendered_bytes(name, expected):
    """Golden digests captured before this layout change, including canonical blocks."""
    f, a = baseline_sections()
    sources = {"S1": sample_source()}
    if name == "legacy_v1":
        rendered = render_document(draft_input(), f, a, sources)
    elif name == "study_readable_v2":
        rendered = render_readable(draft_input(), f, a, sources, profile_snapshot(name), [])
    else:
        rendered = render_topic(make_plan(f, a), f, a, sources, prior_topic_profile(), [])
    assert digest(dumps(rendered)) == expected


@pytest.mark.parametrize("saved_profile", [prior_topic_profile, prior_grouping_profile])
def test_new_policy_preserves_prior_v3_preview_hash_and_approval(service, monkeypatch, saved_profile):
    actual = StudyService(service.root)
    with monkeypatch.context() as patch:
        patch.setattr("cs_study_mcp.service.profile_snapshot", lambda _: saved_profile())
        job_id = actual.create_study("이전 v3 합성 작업")["job_id"]
    actual.save_research(job_id, [sample_source()])
    f, a = readable_sections()
    actual.save_knowledge_section(job_id, f, 0)
    actual.save_knowledge_section(job_id, a, 0)
    plan = make_plan(f, a)
    preview = actual.prepare_document_preview(job_id, plan, 0)
    review(actual, job_id, preview)
    draft = promote(actual, job_id, preview)
    approve_v2(actual, job_id, draft)
    before = actual.get_study(job_id)

    new_job = actual.create_study("새 목록 구성 합성 작업")
    assert new_job["presentation_profile"]["content_unit_layout_version"] == 1
    assert new_job["presentation_profile"]["topic_grouping_policy_version"] == 1
    assert "topic_grouping_policy_version" not in before["presentation_profile"]
    for name in ("legacy_v1", "study_readable_v2"):
        assert "content_unit_layout_version" not in profile_snapshot(name)
        assert "topic_grouping_policy_version" not in profile_snapshot(name)
    restored = actual.prepare_document_preview(job_id, plan, preview["version"])
    assert restored["idempotent"] and restored["current"]
    assert restored["version"] == preview["version"]
    assert restored["presentation_hash"] == preview["presentation_hash"]
    assert restored["markdown"] == preview["markdown"]
    assert actual.get_study(job_id) == before
    assert actual.prepare_publication(job_id, draft["version"])["action"] == "create_page"


def concept_render(tmp_path, *, topic_toggles=False):
    f, a = baseline_sections()
    source = sample_source()
    f.principles[0].key_point = "모의 결과를 저장한 뒤 재사용합니다."
    f.principles[0].body = (
        "- **필요성:** 반복 요청을 처리하는 합성 설명입니다. 두 문장은 하나의 요점을 설명합니다.\n"
        "- **동작:**\n"
        "  - 저장된 결과를 먼저 확인합니다.\n"
        "  - 없으면 새로 구하는 가상 상황입니다.\n\n"
        "```http\nGET /example HTTP/1.1\n    indented fixture\n```"
    )
    a.concepts[0].body = "- **조건:** 모의 적용 조건을 확인합니다.\n- **예외:** 모의 예외도 보존합니다."
    image_bytes = b"synthetic image bytes, not a live screenshot"
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    path = tmp_path / "synthetic.png"
    path.write_bytes(image_bytes)
    f.visuals = [
        Visual(
            id="flow", kind="flow", title="합성 흐름",
            placement=Placement(section="foundation", after_id=f.principles[0].id),
            caption="합성 흐름의 경계와 조건을 보존합니다.", alt_text="두 모의 단계",
            claim_ids=["F1"], diagram_spec="flowchart LR\nA-->B",
        ),
        Visual(
            id="screen", kind="screenshot", title="합성 이미지",
            placement=Placement(section="foundation", after_id=f.examples[0].id),
            caption="합성 이미지 캡션", alt_text="합성 이미지의 설명", claim_ids=["F1"],
            asset_hash=image_hash,
            screenshot=ScreenshotSource(
                source_id="S1", image_url="https://example.org/synthetic.png",
                locator="합성 이미지 위치", accessed_at=source.accessed_at,
                usage_note="실제 공개 이미지가 아닌 테스트 fixture",
            ),
        ),
    ]
    plan = make_plan(f, a)
    refs = [OutlineItemRef(section=role, item_id=item_id) for role, item_id in primary_items(f, a)]
    plan.outline = [
        OutlineGroup(id="core", title="핵심 흐름", display="heading",
                     children=[ref for ref in refs if ref.section == "foundation"]),
        OutlineGroup(id="detail", title="심화 조건·예외", display="toggle",
                     children=[ref for ref in refs if ref.section == "advanced"]),
    ]
    if topic_toggles:
        plan.outline[0].display = "toggle"
        plan.outline[0].children.append(plan.outline.pop())
    assets = [{"section": "foundation", "visual_id": "screen", "hash": image_hash,
               "path": str(path), "remote_url": str(f.visuals[1].screenshot.image_url)}]
    return f, a, render_topic(plan, f, a, {"S1": source}, profile_snapshot("study_topic_v3"), assets), assets


def test_topic_toggle_keeps_overview_open_and_rich_children_in_two_levels(tmp_path):
    f, _, rendered, assets = concept_render(tmp_path, topic_toggles=True)
    blocks = rendered["blocks"]
    subject = next(b for b in blocks if b["type"] == "toggle" and b["title"] == "핵심 흐름")
    before = blocks[:blocks.index(subject)]
    assert any(b["type"] == "heading" and b["text"] == "학습 목표·선수지식" for b in before)
    children = list(flattened(subject["children"]))
    assert any(b["type"] == "toggle" and b["title"] == "심화 조건·예외" for b in children)
    assert any(b["type"] == "paragraph" and f.principles[0].key_point in b["text"] for b in children)
    assert any(b["type"] == "list_item" and b["indent"] == 2 and "먼저 확인" in b["text"] for b in children)
    for kind in ("table", "image"):
        assert any(b["type"] == kind for b in children)
    for language in ("http", "mermaid"):
        assert any(b.get("language") == language for b in children)
    assert not any(b["type"] == "quote" for b in flattened(blocks))
    observed = [{"remote_url": assets[0]["remote_url"], "hash": assets[0]["hash"]}]
    assert verify_readback(blocks, rendered["notion_markdown"], expected_assets=assets, observed_assets=observed)["valid"]


def test_content_units_core_then_example_then_optional_detail_preserve_all_blocks(tmp_path):
    f, a, rendered, _ = concept_render(tmp_path)
    markdown = rendered["markdown"]
    assert markdown.index(f.principles[0].key_point) < markdown.index("- **필요성:")
    assert markdown.index("- **필요성:") < markdown.index(f.examples[0].title)
    assert markdown.index(f.examples[0].title) < markdown.index("<summary>심화 조건·예외</summary>")
    assert f.principles[0].body in markdown
    assert f"- **필요한 이유:** {f.terms[0].why}\n- **짧은 예시:** {f.terms[0].example}" in markdown
    blocks = list(flattened(rendered["blocks"]))
    assert any(b["type"] == "heading" and b["text"] == "핵심 흐름" for b in blocks)
    assert any(b["type"] == "toggle" and b["title"] == "심화 조건·예외" for b in blocks)
    assert sum(b["type"] == "list_item" and "두 문장은 하나의 요점" in b["text"] for b in blocks) == 1
    assert any(b["type"] == "list_item" and b["indent"] == 2 and "먼저 확인" in b["text"] for b in blocks)
    for value in (a.tradeoffs[0].choose_when, a.cases[0].outcome, a.cases[0].interpretation):
        assert any(b["type"] == "list_item" and b["text"].endswith(value) for b in blocks)
    assert next(b for b in blocks if b.get("language") == "http")["text"] == "GET /example HTTP/1.1\n    indented fixture"
    assert next(b for b in blocks if b.get("language") == "mermaid")["text"] == "flowchart LR\nA-->B"
    assert next(b for b in blocks if b["type"] == "table")["rows"] == [["A", "B"]]
    assert sum(b["type"] == "image" for b in blocks) == 1
    assert any(b.get("text") == "합성 이미지 캡션" for b in blocks)
    assert a.questions[0].answer in markdown


def test_content_unit_readback_rejects_missing_or_flattened_lists(tmp_path):
    _, _, rendered, assets = concept_render(tmp_path)
    observed = [{"remote_url": assets[0]["remote_url"],
                 "hash": hashlib.sha256(Path(assets[0]["path"]).read_bytes()).hexdigest()}]
    def verify(text):
        return verify_readback(rendered["blocks"], text, expected_assets=assets, observed_assets=observed)
    assert verify(rendered["notion_markdown"])["valid"]
    assert not verify(rendered["notion_markdown"].replace("  - 저장된 결과", "- 저장된 결과"))["valid"]
    assert not verify(rendered["notion_markdown"].replace("- **동작:**\n", ""))["valid"]


@pytest.mark.parametrize("value", [
    "- 첫 번째 요점\n  - 관련 조건",
    "```text\nfirst\n    second\n```",
    "| A | B |\n| --- | --- |\n| x | y |",
])
def test_labeled_field_rich_markdown_remains_an_authored_block(value):
    f, a = baseline_sections()
    a.tradeoffs[0].advantages = value
    rendered = render_topic(make_plan(f, a), f, a, {"S1": sample_source()}, profile_snapshot("study_topic_v3"), [])
    blocks = list(flattened(rendered["blocks"]))
    assert any(b["type"] == "list_item" and b["text"] == "**장점:**" for b in blocks)
    assert all(b in blocks for b in canonicalize_markdown(value))


def test_new_renderer_does_not_split_authored_prose_into_sentence_bullets():
    f, a = baseline_sections()
    f.principles[0].body = "첫 문장입니다. 같은 요점을 설명하는 두 번째 문장입니다."
    rendered = render_topic(make_plan(f, a), f, a, {"S1": sample_source()}, profile_snapshot("study_topic_v3"), [])
    assert sum(b["type"] == "paragraph" and b["text"] == f.principles[0].body
               for b in flattened(rendered["blocks"])) == 1


def test_authored_answer_blocks_and_prerequisite_units_are_not_joined_to_labels():
    f, a = baseline_sections()
    f.prerequisites = ["스레드는 실행 흐름입니다.", "공유 상태는 함께 쓰는 값입니다."]
    question = f.questions[0]
    question.answer = "- 첫 요점\n  - 그 요점의 조건\n- 둘째 요점"
    question.explanation = (
        "```text\nfirst\n    second\n```\n\n"
        "| A | B |\n| --- | --- |\n| x | y |"
    )
    rendered = render_topic(
        make_plan(f, a), f, a, {"S1": sample_source()}, profile_snapshot("study_topic_v3"), []
    )
    markdown = rendered["markdown"]
    assert "**선수지식:**\n\n- 스레드는" in markdown
    assert f"**모범답안:**\n\n{question.answer}" in markdown
    assert f"**해설:**\n\n{question.explanation}" in markdown
    blocks = list(flattened(rendered["blocks"]))
    assert all(any(block["type"] == "list_item" and block["text"] == value
                   and block["indent"] == 0 for block in blocks)
               for value in f.prerequisites)
    assert all(block in blocks for block in canonicalize_markdown(question.answer))
    assert all(block in blocks for block in canonicalize_markdown(question.explanation))
    assert verify_readback(rendered["blocks"], rendered["notion_markdown"])["valid"]


@pytest.mark.parametrize("profile", [profile_snapshot("study_readable_v2"), prior_topic_profile()])
def test_prior_profiles_keep_inline_prerequisites_and_answer_labels(profile):
    f, a = baseline_sections()
    f.prerequisites = ["첫 전제", "둘째 전제"]
    f.questions[0].answer = "- 첫 요점\n- 둘째 요점"
    if profile["name"] == "study_topic_v3":
        rendered = render_topic(make_plan(f, a), f, a, {"S1": sample_source()}, profile, [])
    else:
        rendered = render_readable(draft_input(), f, a, {"S1": sample_source()}, profile, [])
    assert "**선수지식:** 첫 전제, 둘째 전제" in rendered["markdown"]
    assert "**모범답안:** - 첫 요점\n- 둘째 요점" in rendered["markdown"]
