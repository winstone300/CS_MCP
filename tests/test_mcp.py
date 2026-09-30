import sys

import pytest
from conftest import sample_sections
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cs_study_mcp.server import TOOLS_BY_ROLE, create_server
from cs_study_mcp.service import StudyService


@pytest.mark.parametrize("role", list(TOOLS_BY_ROLE))
async def test_real_stdio_handshake_and_role_tool_list(service, role):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cs_study_mcp", "--project", str(service.root), "serve", "--role", role],
        env={"PYTHONUTF8": "1"},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        catalog = await session.list_tools()
        assert {tool.name for tool in catalog.tools} == TOOLS_BY_ROLE[role]
        assert not any("notion-" in tool.name for tool in catalog.tools)
        runtime = await session.call_tool("get_runtime_info", {})
        assert runtime.structuredContent["role"] == role
        assert set(runtime.structuredContent["tools"]) == TOOLS_BY_ROLE[role]
        if role == "main":
            result = await session.call_tool("create_study", {"topic": "실제 stdio 왕복"})
            assert not result.isError
            job_id = result.structuredContent["job_id"]
            result = await session.call_tool("get_study", {"job_id": job_id})
            assert result.structuredContent["topic"] == "실제 stdio 왕복"
            denied = await session.call_tool(
                "prepare_publication", {"job_id": job_id, "draft_version": 1}
            )
            assert denied.isError
        if role == "foundation":
            result = await session.call_tool("get_question_blueprint", {"role": role})
            assert result.structuredContent["types"] == {"concept": 2, "comparison": 1}


async def test_foundation_cannot_save_advanced_or_impersonate_review(populated):
    service, job_id = populated
    server = create_server(service.root, "foundation")
    with pytest.raises(Exception, match="role_forbidden"):
        await server.call_tool(
            "save_knowledge_section",
            {
                "job_id": job_id,
                "content": sample_sections()[1].model_dump(mode="json"),
                "expected_version": 1,
            },
        )
    with pytest.raises(Exception, match="role_forbidden"):
        await server.call_tool(
            "record_cross_review",
            {
                "job_id": job_id,
                "review": {
                    "reviewer": "advanced",
                    "foundation_version": 1,
                    "advanced_version": 1,
                    "research_revision": 1,
                    "summary": "대리 기록",
                },
            },
        )


@pytest.mark.parametrize("role", list(TOOLS_BY_ROLE))
async def test_presentation_tools_readonly_annotations_and_role_boundaries(service, role):
    server = create_server(service.root, role)
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert tools["get_document_blueprint"].annotations.readOnlyHint is True
    assert tools["get_document_blueprint"].annotations.idempotentHint is True
    if role != "research":
        assert tools["validate_presentation"].annotations.readOnlyHint is True
    else:
        assert "validate_presentation" not in tools
    if role == "notion_writer":
        assert tools["get_publication_payload"].annotations.readOnlyHint is True
        assert tools["get_publication_payload"].annotations.idempotentHint is True
    else:
        assert "get_publication_payload" not in tools
        with pytest.raises(Exception, match="Unknown tool"):
            await server.call_tool("get_publication_payload", {})
    writers = {
        "register_visual_asset": "main",
        "prepare_visual_assets": "main",
        "record_publication_asset": "notion_writer",
    }
    for name, owner in writers.items():
        if role == owner:
            assert tools[name].annotations.readOnlyHint is False
        else:
            assert name not in tools
            with pytest.raises(Exception, match="Unknown tool"):
                await server.call_tool(name, {})


async def test_presentation_read_tools_do_not_create_assets_or_change_study(service):
    actual = StudyService(service.root)
    job_id = actual.create_study("v2 조회 fixture")["job_id"]
    before = actual.get_study(job_id)
    server = create_server(service.root, "foundation")
    await server.call_tool("get_document_blueprint", {"job_id": job_id, "role": "foundation"})
    await server.call_tool("validate_presentation", {"job_id": job_id})
    assert actual.get_study(job_id) == before
    assert not (service.root / ".cs-study/assets").exists()


async def test_mcp_schemas_expose_visual_layout_and_bundle_evidence(service):
    main_tools = {
        tool.name: tool for tool in await create_server(service.root, "main").list_tools()
    }
    creation = main_tools["create_study"].inputSchema["properties"]
    assert creation["presentation_profile"]["default"] == "study_readable_v2"
    assert creation["visual_transport"]["enum"] == ["mermaid", "image"]
    assert main_tools["get_document_blueprint"].inputSchema["required"] == ["job_id", "role"]
    assert (
        main_tools["prepare_visual_assets"].inputSchema["properties"]["expected_versions"][
            "additionalProperties"
        ]["type"]
        == "integer"
    )
    assert (
        "bundle_hash"
        in main_tools["record_review"].inputSchema["$defs"]["ReviewInput"]["properties"]
    )
    foundation_tools = {
        tool.name: tool for tool in await create_server(service.root, "foundation").list_tools()
    }
    schema = foundation_tools["save_knowledge_section"].inputSchema["$defs"]
    assert {"comparisons", "visuals"} <= schema["FoundationSection"]["properties"].keys()
    assert {"id", "key_point", "related_item_id"} <= schema["Explanation"]["properties"].keys()
    assert {"diagram_spec", "asset_hash", "screenshot", "claim_ids", "placement"} <= schema[
        "Visual"
    ]["properties"].keys()
    assert {"section", "after_id"} <= schema["Placement"]["properties"].keys()
    writer_tools = {
        tool.name: tool for tool in await create_server(service.root, "notion_writer").list_tools()
    }
    publication = writer_tools["record_publication"].inputSchema["$defs"]["PublicationInput"][
        "properties"
    ]
    assert {"observed_blocks", "observed_assets", "observed_visual_check"} <= publication.keys()
    assert publication["observed_visual_check"]["default"] is False
