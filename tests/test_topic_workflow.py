"""Synthetic composition and approval tests; no live accounts or publishing."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from conftest import PAGE_ID, PARENT_ID, draft_input, sample_source
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_presentation_workflow import approve_v2, readable_sections

from cs_study_mcp.models import CrossReview, DocumentPlanInput, PreviewReference, PublicationInput
from cs_study_mcp.service import StudyService, WorkflowError
from cs_study_mcp.storage import dumps
from cs_study_mcp.topic import primary_items


def make_plan(f, a, **overrides):
    refs = [
        {"type": "item", "section": role, "item_id": item_id}
        for role, item_id in primary_items(f, a)
    ]
    data = {
        **draft_input().model_dump(),
        "research_revision": 1,
        "outline": [
            {
                "type": "group",
                "id": "http",
                "title": "HTTP",
                "display": "heading",
                "children": [
                    {"type": "group", "id": "request", "title": "HTTP Request", "children": refs}
                ],
            }
        ],
    }
    return DocumentPlanInput.model_validate({**data, **overrides})


@pytest.fixture
def topic(service):
    actual = StudyService(service.root)
    job_id = actual.create_study("합성 HTTP 구성 시험")["job_id"]
    actual.save_research(job_id, [sample_source()])
    f, a = readable_sections()
    f.principles[
        0
    ].body = "요청 메시지 예시입니다.\n\n```http\nGET /example HTTP/1.1\nHost: example.org\n\n```"
    actual.save_knowledge_section(job_id, f, 0)
    actual.save_knowledge_section(job_id, a, 0)
    return actual, job_id, make_plan(f, a)


def review(service, job_id, preview):
    content = preview["content"]
    for role in ("foundation", "advanced"):
        service.record_cross_review(
            job_id,
            CrossReview(
                reviewer=role,
                foundation_version=content["foundation_version"],
                advanced_version=content["advanced_version"],
                research_revision=content["research_revision"],
                presentation_hash=preview["presentation_hash"],
                summary="합성 테스트 검토",
            ),
        )


def promote(service, job_id, preview):
    return service.save_draft(
        job_id,
        PreviewReference(
            preview_version=preview["version"], presentation_hash=preview["presentation_hash"]
        ),
    )


def test_preview_before_review_and_exact_candidate_promotion(topic):
    service, job_id, plan = topic
    assert service.get_study(job_id)["presentation_profile"]["name"] == "study_topic_v3"
    p = service.prepare_document_preview(job_id, plan, 0)
    assert p["current"] and p["version"] == 1
    assert service.get_study(job_id)["draft"] is None
    html = Path(p["paths"]["html"]).read_text(encoding="utf-8")
    assert "<details>" in html and "<table" in html and "GET /example" in html
    assert '\t<table header-row="true">' in p["notion_markdown"]
    with pytest.raises(WorkflowError, match="cross_review_required"):
        promote(service, job_id, p)
    with pytest.raises(WorkflowError, match="preview_required"):
        service.save_draft(job_id, draft_input())
    review(service, job_id, p)
    draft = promote(service, job_id, p)
    assert draft["hash_kind"] == "presentation_bundle_v3"
    actual = service.get_study(job_id)["draft"]
    assert actual["markdown"] == p["markdown"]
    assert actual["notion_markdown"] == p["notion_markdown"]
    assert actual["preview_html"] == html
    approve_v2(service, job_id, draft)
    assert service.prepare_publication(job_id, draft["version"])["action"] == "create_page"
    with pytest.raises(WorkflowError, match="publication_locked"):
        service.prepare_document_preview(job_id, plan, 1)
    assert service.prepare_publication(job_id, draft["version"])["action"] == "inspect_before_retry"


@pytest.mark.parametrize("change", ["title", "summary", "outline"])
def test_presentation_only_changes_invalidate_reviews_and_approval(topic, change):
    service, job_id, plan = topic
    p = service.prepare_document_preview(job_id, plan, 0)
    review(service, job_id, p)
    draft = promote(service, job_id, p)
    approve_v2(service, job_id, draft)
    if change == "title":
        plan.title = "새 제목"
    elif change == "summary":
        plan.summary[0].text = "수정된 요약"
    else:
        plan.outline[0].children[0].children.reverse()
    updated = service.prepare_document_preview(job_id, plan, 1)
    assert updated["presentation_hash"] != p["presentation_hash"]
    assert service.get_study(job_id)["draft"] is None
    assert not service.get_document_preview(job_id, 1)["current"]
    with pytest.raises(WorkflowError):
        service.prepare_publication(job_id, draft["version"])
    with pytest.raises(WorkflowError, match="stale_visual_review"):
        review(service, job_id, p)
    with pytest.raises(WorkflowError, match="stale_visual_review"):
        promote(service, job_id, updated)
    with pytest.raises(WorkflowError, match="stale_preview"):
        promote(service, job_id, p)
    review(service, job_id, updated)
    promote(service, job_id, updated)


def test_same_candidate_preserves_approval_and_repairs_export(topic):
    service, job_id, plan = topic
    p = service.prepare_document_preview(job_id, plan, 0)
    review(service, job_id, p)
    draft = promote(service, job_id, p)
    approve_v2(service, job_id, draft)
    Path(p["paths"]["html"]).write_text("modified", encoding="utf-8")
    assert not service.validate_presentation(job_id)["valid"]
    with pytest.raises(WorkflowError):
        service.validate_publication_bundle(job_id)
    again = service.prepare_document_preview(job_id, plan, 1)
    assert again["idempotent"] and again["presentation_hash"] == p["presentation_hash"]
    assert service.get_study(job_id)["status"] == "approved"
    service.validate_publication_bundle(job_id)


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "missing_outline_item"),
        ("duplicate", "duplicate_outline_item"),
        ("unknown", "unknown_outline_item"),
        ("group", "duplicate_group"),
    ],
)
def test_invalid_outline_is_rejected(topic, change, code):
    service, job_id, plan = topic
    refs = plan.outline[0].children[0].children
    if change == "missing":
        refs.pop()
    elif change == "duplicate":
        refs.append(refs[0])
    elif change == "unknown":
        refs[0].item_id = "absent"
    else:
        plan.outline[0].children[0].id = plan.outline[0].id
    with pytest.raises(WorkflowError, match=code):
        service.prepare_document_preview(job_id, plan, 0)


@pytest.mark.parametrize("change", ["research", "section", "candidate"])
def test_render_race_rejects_changed_dependencies(topic, monkeypatch, change):
    import cs_study_mcp.render_v3 as renderer
    from cs_study_mcp.models import FoundationSection

    service, job_id, plan = topic
    original = renderer.render_topic
    entered, release = Event(), Event()

    def slow(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(renderer, "render_topic", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.prepare_document_preview, job_id, plan, 0)
        try:
            assert entered.wait(5)
            if change == "research":
                service.save_research(job_id, [sample_source("S2")])
            elif change == "section":
                f = FoundationSection.model_validate(
                    service.get_knowledge_section(job_id, "foundation")["content"]
                )
                f.principles[0].body += "\n추가 문장"
                service.save_knowledge_section(job_id, f, 1)
            else:
                monkeypatch.setattr(renderer, "render_topic", original)
                service.prepare_document_preview(job_id, plan, 0)
        finally:
            release.set()
        with pytest.raises(WorkflowError, match="version_conflict"):
            pending.result()


@pytest.mark.parametrize("field", ["plan", "rendered", "dependencies"])
def test_candidate_tampering_blocks_bundle(topic, field):
    service, job_id, plan = topic
    p = service.prepare_document_preview(job_id, plan, 0)
    review(service, job_id, p)
    draft = promote(service, job_id, p)
    approve_v2(service, job_id, draft)
    with service.db.connect(write=True) as db:
        row = db.execute("SELECT * FROM document_previews WHERE job_id=?", (job_id,)).fetchone()
        value = json.loads(row[field])
        value["tampered"] = True
        db.execute(f"UPDATE document_previews SET {field}=? WHERE job_id=?", (dumps(value), job_id))
    with pytest.raises(WorkflowError):
        service.validate_publication_bundle(job_id)
    with pytest.raises(WorkflowError):
        service.prepare_publication(job_id, draft["version"])


async def test_preview_tools_have_scoped_permissions(topic):
    from cs_study_mcp.server import create_server

    service, job_id, plan = topic
    p = service.prepare_document_preview(job_id, plan, 0)
    before = service.get_study(job_id)
    for role in ("main", "research", "foundation", "advanced", "notion_writer"):
        server = create_server(service.root, role)
        tools = {t.name: t for t in await server.list_tools()}
        assert ("prepare_document_preview" in tools) == (role == "main")
        assert ("get_document_preview" in tools) == (role in ("main", "foundation", "advanced"))
        if "get_document_preview" in tools:
            assert tools["get_document_preview"].annotations.readOnlyHint
            await server.call_tool(
                "get_document_preview", {"job_id": job_id, "version": p["version"]}
            )
        if role != "main":
            with pytest.raises(Exception, match="Unknown tool"):
                await server.call_tool("prepare_document_preview", {})
    assert service.get_study(job_id) == before


def test_v3_synthetic_publication_roundtrip_preserves_nested_blocks(topic):
    service, job_id, plan = topic
    p = service.prepare_document_preview(job_id, plan, 0)
    review(service, job_id, p)
    draft = promote(service, job_id, p)
    approve_v2(service, job_id, draft)
    intent = service.prepare_publication(job_id, draft["version"])
    service.record_publication(
        job_id,
        PublicationInput(
            attempt_id=intent["attempt_id"],
            outcome="page_created",
            page_id=PAGE_ID,
            page_url=f"https://notion.so/{PAGE_ID}",
        ),
    )
    response = service.record_publication(
        job_id,
        PublicationInput(
            attempt_id=intent["attempt_id"],
            outcome="verified",
            page_id=PAGE_ID,
            page_url=f"https://notion.so/{PAGE_ID}",
            observed_title=plan.title,
            observed_parent_page_id=PARENT_ID,
            observed_markdown=intent["markdown"],
            observed_visual_check=True,
        ),
    )  # Synthetic observation, never a live Notion claim.
    assert response["status"] == "completed"
    assert service.prepare_publication(job_id, draft["version"])["action"] == "already_completed"


def test_restart_and_optimistic_version_conflict(topic):
    service, job_id, plan = topic
    p = service.prepare_document_preview(job_id, plan, 0)
    restarted = StudyService(service.root)
    assert restarted.get_document_preview(job_id, 1)["presentation_hash"] == p["presentation_hash"]
    assert restarted.get_study(job_id)["document_preview"]["version"] == 1
    with pytest.raises(WorkflowError, match="version_conflict"):
        restarted.prepare_document_preview(job_id, plan, 0)
    changed = plan.model_copy(deep=True)
    changed.research_revision = 0
    with pytest.raises(WorkflowError, match="stale_preview"):
        restarted.prepare_document_preview(job_id, changed, 1)


def test_changed_stored_draft_title_cannot_bypass_reviewed_candidate(topic):
    service, job_id, plan = topic
    p = service.prepare_document_preview(job_id, plan, 0)
    review(service, job_id, p)
    draft = promote(service, job_id, p)
    approve_v2(service, job_id, draft)
    with service.db.connect(write=True) as db:
        row = db.execute("SELECT content FROM drafts WHERE job_id=?", (job_id,)).fetchone()
        content = json.loads(row["content"])
        content["title"] = "검토하지 않은 제목"
        db.execute("UPDATE drafts SET content=? WHERE job_id=?", (dumps(content), job_id))
    with pytest.raises(WorkflowError, match="bundle_changed"):
        service.prepare_publication(job_id, draft["version"])


def test_export_failure_can_resume_same_candidate_without_extra_version(topic, monkeypatch):
    import cs_study_mcp.presentation_service as presentation

    service, job_id, plan = topic
    original = presentation.atomic_text

    def broken(*args):
        raise OSError("mock disk unavailable")

    monkeypatch.setattr(presentation, "atomic_text", broken)
    result = service.prepare_document_preview(job_id, plan, 0)
    assert result["version"] == 1 and "export_error" in result
    assert not service.get_document_preview(job_id, 1)["current"]
    monkeypatch.setattr(presentation, "atomic_text", original)
    repaired = service.prepare_document_preview(job_id, plan, 1)
    assert repaired["version"] == 1 and repaired["idempotent"] and repaired["current"]


def test_asset_mutation_after_review_blocks_approval(topic, monkeypatch):
    from test_assets import PNG

    from cs_study_mcp.assets import store_asset
    from cs_study_mcp.models import AdvancedSection, FoundationSection, Placement, Visual

    service, job_id, _ = topic
    source = service.root / "fixture.png"
    source.write_bytes(PNG)
    asset = store_asset(service.root, source, generated=True)
    monkeypatch.setattr(
        "cs_study_mcp.assets.render_diagram",
        lambda *args: {**asset, "spec_hash": "fixture", "renderer_fingerprint": "fixture"},
    )
    f = FoundationSection.model_validate(
        service.get_knowledge_section(job_id, "foundation")["content"]
    )
    a = AdvancedSection.model_validate(service.get_knowledge_section(job_id, "advanced")["content"])
    f.visuals = [
        Visual(
            id="flow",
            kind="flow",
            title="모의 그림",
            caption="모의 캡션",
            alt_text="모의 설명",
            diagram_spec="flowchart LR\nA-->B",
            claim_ids=["F1"],
            placement=Placement(section="foundation", after_id=f.principles[0].id),
        )
    ]
    service.save_knowledge_section(job_id, f, 1)
    a.foundation_version = 2
    service.save_knowledge_section(job_id, a, 1)
    prepared = service.prepare_visual_assets(job_id, {"foundation": 2, "advanced": 2})
    assert prepared["valid"] and "next_step" in prepared
    plan = make_plan(f, a, foundation_version=2, advanced_version=2)
    p = service.prepare_document_preview(job_id, plan, 0)
    review(service, job_id, p)
    draft = promote(service, job_id, p)
    (service.root / asset["path"]).write_bytes(PNG + b"changed")
    with pytest.raises(WorkflowError):
        approve_v2(service, job_id, draft)


async def test_real_mcp_roundtrip_prepares_and_promotes_recursive_composition(topic):
    service, job_id, plan = topic
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "cs_study_mcp", "--project", str(service.root), "serve", "--role", "main"],
        env={"PYTHONUTF8": "1"})
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("prepare_document_preview", {
            "job_id": job_id, "content": plan.model_dump(mode="json"), "expected_version": 0})
        assert not result.isError
        p = result.structuredContent
        review(service, job_id, p)
        promoted = await session.call_tool("save_draft", {"job_id": job_id, "content": {
            "preview_version": p["version"], "presentation_hash": p["presentation_hash"]}})
        assert not promoted.isError
        assert promoted.structuredContent["markdown"] == p["markdown"]


def test_v3_profile_and_rules_are_frozen_for_existing_jobs(topic, monkeypatch):
    from cs_study_mcp.presentation import profile_snapshot

    service, job_id, _ = topic
    before = service.get_study(job_id)
    changed = profile_snapshot("study_topic_v3")
    changed["guidance"]["explanation"] = "새 작업용 설명 지침"
    monkeypatch.setattr("cs_study_mcp.service.profile_snapshot", lambda _: changed)
    (service.root / "AGENTS.md").write_text("새 작업용 규칙", encoding="utf-8")
    other = service.create_study("새 주제")
    assert other["rules_hash"] != before["rules_hash"]
    assert other["presentation_profile"] == changed
    assert service.get_study(job_id) == before
