"""Rendering contracts for grouped content, never live CS fact verification."""

from copy import deepcopy

import pytest
from conftest import sample_source
from test_presentation_workflow import readable_sections
from test_topic_workflow import make_plan

from cs_study_mcp.models import OutlineGroup, OutlineItemRef, Placement, Visual
from cs_study_mcp.presentation import profile_snapshot
from cs_study_mcp.publication import PublicationFormatError, canonicalize_markdown
from cs_study_mcp.render_v3 import render_topic
from cs_study_mcp.topic import outline_issues


def flattened(blocks):
    for block in blocks:
        yield block
        yield from flattened(block.get("children", []))


def test_three_nested_toggles_preserve_code_table_diagram_and_caption():
    f, a = readable_sections()
    f.principles[0].body = "```http\nGET / HTTP/1.1\nHost: example.org\n    indented\n```"
    f.visuals = [
        Visual(
            id="flow",
            kind="flow",
            title="모의 요청 흐름",
            caption="캡션 보존 확인",
            alt_text="모의 화살표",
            claim_ids=["F1"],
            diagram_spec="flowchart LR\nA-->B",
            placement=Placement(section="foundation", after_id=f.principles[0].id),
        )
    ]
    plan = make_plan(f, a)
    group = plan.outline[0]
    group.display = "toggle"
    group.children[0].children = [
        OutlineGroup(id="message", title="메시지 구성", children=group.children[0].children)
    ]
    report = outline_issues(plan, f, a)
    assert not report["errors"] and report["warnings"][0]["code"] == "deep_toggle"
    result = render_topic(
        plan, f, a, {"S1": sample_source()}, profile_snapshot("study_topic_v3"), []
    )
    blocks = list(flattened(result["blocks"]))
    code = next(b for b in blocks if b.get("language") == "http")
    assert code["text"].endswith("    indented")
    table = next(b for b in blocks if b["type"] == "table")
    assert table["columns"] == ["조건", "결과"] and table["rows"] == [["A", "B"]]
    assert table["header_row"] is True
    assert sum(b.get("language") == "mermaid" for b in blocks) == 1
    assert sum(b.get("text") == "캡션 보존 확인" for b in blocks) == 1
    assert sum(b["type"] == "toggle" for b in blocks) == 9  # 3 topic groups + 6 answer toggles
    ordinary = canonicalize_markdown(result["markdown"])
    assert next(b for b in flattened(ordinary) if b.get("language") == "http") == code


def test_reordered_items_keep_attached_misconceptions_and_materials_once():
    f, a = readable_sections()
    plan = make_plan(f, a)
    plan.outline[0].children[0].children.reverse()
    result = render_topic(
        plan, f, a, {"S1": sample_source()}, profile_snapshot("study_topic_v3"), []
    )
    blocks = list(flattened(result["blocks"]))
    headings = [b["text"] for b in blocks if b["type"] == "heading"]
    assert headings.index(a.cases[0].title) < headings.index(f.terms[0].name)
    assert headings.count("흔한 오해: " + f.misconceptions[0].heading) == 1
    assert sum(b["type"] == "table" for b in blocks) == 1


def test_role_qualified_ids_do_not_collide():
    f, a = readable_sections()
    a.concepts[0].id = f.terms[0].id
    plan = make_plan(f, a)
    assert not outline_issues(plan, f, a)["errors"]
    rendered = render_topic(
        plan, f, a, {"S1": sample_source()}, profile_snapshot("study_topic_v3"), []
    )
    assert f.terms[0].definition in rendered["markdown"]
    assert a.concepts[0].body in rendered["markdown"]
    bad = deepcopy(plan)
    bad.outline[0].children[0].children.append(
        OutlineItemRef(section="foundation", item_id=f.misconceptions[0].id)
    )
    assert "unknown_outline_item" in {e["code"] for e in outline_issues(bad, f, a)["errors"]}


@pytest.mark.parametrize("body", ["```http\nnot closed", "<details>\n<summary>broken</summary>"])
def test_unclosed_authored_markup_is_not_silently_published(body):
    f, a = readable_sections()
    f.principles[0].body = body
    with pytest.raises(PublicationFormatError):
        render_topic(
            make_plan(f, a), f, a, {"S1": sample_source()}, profile_snapshot("study_topic_v3"), []
        )
