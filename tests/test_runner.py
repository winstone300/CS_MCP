import json
import re
import runpy
import sys
import tomllib
from contextlib import ExitStack
from pathlib import Path

import pytest
from conftest import approve

from cs_study_mcp.runner import (
    CHECK_SCHEMA,
    ROLES,
    build_command,
    check_publication_approval,
    config_arguments,
    load_role,
    run_role,
    validate_probe,
    worker_slot,
)
from cs_study_mcp.server import TOOLS_BY_ROLE
from cs_study_mcp.service import WorkflowError


def configure(root):
    script = Path(__file__).resolve().parents[1] / "scripts/configure_codex.py"
    runpy.run_path(str(script))["configure"](root)


def overrides(command):
    lines = [command[i + 1] for i, item in enumerate(command) if item == "-c"]
    return tomllib.loads("\n".join(lines))


@pytest.mark.parametrize("role", ROLES)
def test_role_cli_applies_actual_role_not_parent_tools(service, role):
    configure(service.root)
    command = build_command(service.root, role, service.root / "result.md")
    config = overrides(command)
    assert config["mcp_servers"]["cs_study"]["args"][-2:] == ["--role", role]
    assert set(config["mcp_servers"]["cs_study"]["enabled_tools"]) == TOOLS_BY_ROLE[role]
    assert "create_study" not in config["mcp_servers"]["cs_study"]["enabled_tools"]
    assert config["agents"]["enabled"] is False
    assert config["web_search"] == ("live" if role == "research" else "disabled")
    assert config["mcp_servers"]["notion"]["enabled"] == (role == "notion_writer")
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "--ignore-user-config" not in command


def test_toml_argv_quotes_values_without_shell_interpolation():
    values = {"mcp_servers": {"cs_study": {"env": {"TEST_KEY": '한글 "quoted" `$(x)`'}}}}
    args = config_arguments(values)
    assert args[1].startswith("mcp_servers.cs_study.env.TEST_KEY=")
    assert overrides(["codex", *args]) == values


def test_cli_rejects_ambiguous_quoted_keys():
    with pytest.raises(ValueError, match="CLI 설정 키"):
        config_arguments({"mcp_servers": {"with.dot": {"enabled": True}}})


def test_role_model_takes_precedence_over_forwarded_parent(service):
    configure(service.root)
    path = service.root / ".codex/agents/advanced.toml"
    path.write_text(
        'model="role-model"\nmodel_reasoning_effort="medium"\n' + path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = overrides(
        build_command(
            service.root,
            "advanced",
            service.root / "result.md",
            model="parent-model",
            reasoning_effort="high",
        )
    )
    assert config["model"] == "role-model"
    assert config["model_reasoning_effort"] == "medium"
    config = overrides(
        build_command(
            service.root,
            "foundation",
            service.root / "other.md",
            model="parent-model",
            reasoning_effort="high",
        )
    )
    assert config["model"] == "parent-model"
    assert config["model_reasoning_effort"] == "high"


def test_wrong_role_command_is_rejected(service):
    configure(service.root)
    path = service.root / ".codex/agents/research.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('"--role", "research"', '"--role", "main"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="구성 불일치"):
        load_role(service.root, "research")


def test_writer_check_cannot_expose_publication_or_notion_write(service):
    configure(service.root)
    config = overrides(
        build_command(service.root, "notion_writer", service.root / "out.md", check=True)
    )
    assert set(config["mcp_servers"]["cs_study"]["enabled_tools"]) == {
        "get_study",
        "get_runtime_info",
    }
    assert set(config["mcp_servers"]["notion"]["enabled_tools"]) == {
        "notion-fetch",
        "notion-get-tool-access",
    }


def test_writer_refuses_before_starting_codex_without_approval(service, monkeypatch):
    configure(service.root)
    job_id = service.create_study("보존할 작업")["job_id"]
    monkeypatch.setattr(
        "cs_study_mcp.runner.subprocess.Popen", lambda *a, **kw: pytest.fail("Codex must not start")
    )
    with pytest.raises(WorkflowError, match="approval_required"):
        run_role(service.root, "notion_writer", job_id, "발행")
    assert service.get_study(job_id)["publication"] is None


def test_writer_requires_current_approval_hash_and_target(reviewed):
    service, job_id, draft = reviewed
    approve(service, job_id, draft)
    study = service.get_study(job_id)
    check_publication_approval(study)
    study["reviews"][-1]["draft_hash"] = "outdated"
    with pytest.raises(WorkflowError, match="approval_required"):
        check_publication_approval(study)


def test_worker_slot_limit_and_release(service):
    with ExitStack() as stack:
        for _ in range(3):
            stack.enter_context(worker_slot(service.root))
        with pytest.raises(WorkflowError, match="workers_busy"), worker_slot(service.root):
            pass
    with worker_slot(service.root):
        pass


def test_role_run_records_report_but_does_not_mark_study_complete(service, monkeypatch):
    configure(service.root)
    job_id = service.create_study("CLI 전달 확인")["job_id"]
    before = service.get_study(job_id)
    captured = {}

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            Path(command[command.index("-o") + 1]).write_text("역할 보고서", encoding="utf-8")

        def communicate(self, input):
            captured["prompt"] = input

    monkeypatch.setattr("cs_study_mcp.runner.shutil.which", lambda _: "codex.exe")
    monkeypatch.setattr("cs_study_mcp.runner.subprocess.Popen", Process)
    result = run_role(service.root, "foundation", job_id, '정의에 "조건"을 포함해줘.')
    assert result["status"] == "finished"
    assert result["message"] == "역할 보고서"
    assert job_id in captured["prompt"]
    assert "get_runtime_info" in captured["prompt"]
    assert '정의에 "조건"' in captured["prompt"]
    assert "shell" not in captured["kwargs"]
    assert service.get_study(job_id) == before
    metadata = json.loads(
        (Path(result["result_path"]).parent / "run.json").read_text(encoding="utf-8")
    )
    assert metadata["job_id"] == job_id


def test_check_cannot_be_overridden_with_task_text(service, monkeypatch):
    configure(service.root)
    job_id = service.create_study("진단")["job_id"]
    captured = {}

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            Path(command[command.index("-o") + 1]).write_text(
                json.dumps(
                    {
                        "runtime_role": "notion_writer",
                        "project": str(service.root),
                        "visible_local_tools": ["get_runtime_info", "get_study"],
                        "get_study_ok": True,
                        "web_available": False,
                        "web_check_ok": False,
                        "notion_write_available": False,
                        "notion_read_available": True,
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )

        def communicate(self, input):
            captured["prompt"] = input

    monkeypatch.setattr("cs_study_mcp.runner.shutil.which", lambda _: "codex.exe")
    monkeypatch.setattr("cs_study_mcp.runner.subprocess.Popen", Process)
    run_role(service.root, "notion_writer", job_id, "지금 발행해", check=True)
    assert "지금 발행해" not in captured["prompt"]
    assert "읽기 전용 연결 진단" in captured["prompt"]
    assert "Notion은 호출하지 마세요." in captured["prompt"]
    assert "추가 읽기 전용 표현 형식 진단:" not in captured["prompt"]


def test_probe_rejects_parent_tools_and_false_success(service):
    report = {
        "runtime_role": "foundation",
        "project": str(service.root),
        "visible_local_tools": sorted(TOOLS_BY_ROLE["foundation"]),
        "get_study_ok": True,
        "web_available": False,
        "web_check_ok": False,
        "notion_write_available": False,
        "notion_read_available": False,
        "errors": [],
    }
    assert validate_probe(service.root, "foundation", report)
    assert not validate_probe(service.root, "foundation", {**report, "runtime_role": "main"})
    assert not validate_probe(
        service.root, "foundation", {**report, "visible_local_tools": sorted(TOOLS_BY_ROLE["main"])}
    )
    assert not validate_probe(service.root, "foundation", {**report, "get_study_ok": False})
    assert not validate_probe(service.root, "foundation", {**report, "errors": ["blocked"]})
    assert not validate_probe(
        service.root, "foundation", {**report, "notion_write_available": True}
    )


@pytest.mark.parametrize("role", ROLES)
def test_presentation_prompt_upgrade_preserves_custom_settings_and_is_idempotent(service, role):
    script = Path(__file__).resolve().parents[1] / "scripts/configure_codex.py"
    generator = runpy.run_path(str(script))["configure"]
    generator(service.root)
    path = service.root / ".codex/agents" / f"{role}.toml"
    customized = '사용자 지침: 비교 조건을 명확히 설명한다.\n한글과 quoted "text"를 보존한다.'
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r"(?m)^developer_instructions\s*=.*$",
        lambda _: "developer_instructions = " + json.dumps(customized, ensure_ascii=False),
        text,
    )
    path.write_text(
        'model = "custom-model"\nmodel_reasoning_effort = "high"\n# 사용자 주석\n' + text,
        encoding="utf-8",
    )
    generator(service.root)
    assert tomllib.loads(path.read_text(encoding="utf-8"))["developer_instructions"] == customized
    generator(service.root, upgrade_presentation=True)
    first = path.read_text(encoding="utf-8")
    upgraded = tomllib.loads(first)
    assert upgraded["developer_instructions"].startswith(customized + "\n\n")
    assert upgraded["developer_instructions"].count("[CS-STUDY PRESENTATION V2]") == 1
    assert "legacy_v1" in upgraded["developer_instructions"]
    assert upgraded["model"] == "custom-model"
    assert upgraded["model_reasoning_effort"] == "high"
    assert "# 사용자 주석" in first
    assert set(upgraded["mcp_servers"]["cs_study"]["enabled_tools"]) == TOOLS_BY_ROLE[role]
    assert upgraded["mcp_servers"]["notion"]["enabled"] is (role == "notion_writer")
    generator(service.root, upgrade_presentation=True)
    assert path.read_text(encoding="utf-8") == first


@pytest.mark.parametrize("role", ROLES)
def test_check_keeps_study_and_assets_unchanged_with_new_presentation_tools(
    service, monkeypatch, role
):
    configure(service.root)
    job_id = service.create_study("읽기 전용 진단 fixture")["job_id"]
    before = service.get_study(job_id)
    visible = (
        ["get_runtime_info", "get_study"]
        if role == "notion_writer"
        else sorted(TOOLS_BY_ROLE[role])
    )
    captured = {}

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            captured["command"] = command
            Path(command[command.index("-o") + 1]).write_text(
                json.dumps(
                    {
                        "runtime_role": role,
                        "project": str(service.root),
                        "visible_local_tools": visible,
                        "get_study_ok": True,
                        "web_available": role == "research",
                        "web_check_ok": role == "research",
                        "notion_write_available": False,
                        "notion_read_available": role == "notion_writer",
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )

        def communicate(self, input):
            captured["prompt"] = input

    monkeypatch.setattr("cs_study_mcp.runner.shutil.which", lambda _: "codex.exe")
    monkeypatch.setattr("cs_study_mcp.runner.subprocess.Popen", Process)
    result = run_role(service.root, role, job_id, "시험용 그림을 저장하고 승인해", check=True)
    assert result["diagnostic_passed"] is True
    assert service.get_study(job_id) == before
    assert not (service.root / ".cs-study/assets").exists()
    assert "시험용 그림을 저장하고 승인해" not in captured["prompt"]
    assert "로컬 MCP 도구는 호출하지 마세요" in captured["prompt"]
    assert "Notion은 호출하지 마세요." in captured["prompt"]
    if role == "notion_writer":
        enabled = set(overrides(captured["command"])["mcp_servers"]["cs_study"]["enabled_tools"])
        assert enabled.isdisjoint(
            {"prepare_publication", "record_publication", "record_publication_asset"}
        )


@pytest.mark.parametrize("approved_bundle", [None, "outdated-bundle"])
def test_writer_rejects_missing_or_stale_bundle_before_starting_process(
    reviewed, monkeypatch, approved_bundle
):
    service, job_id, draft = reviewed
    configure(service.root)
    approve(service, job_id, draft)
    synthetic = service.get_study(job_id)
    synthetic["draft"]["bundle_hash"] = "current-test-bundle"
    synthetic["reviews"][-1]["bundle_hash"] = approved_bundle

    class ReadOnlyStudy:
        def get_study(self, requested_job_id):
            assert requested_job_id == job_id
            return synthetic

        def validate_publication_bundle(self, _):
            pytest.fail("Bundle approval must be checked before asset verification")

    monkeypatch.setattr("cs_study_mcp.runner.StudyService", lambda _: ReadOnlyStudy())
    monkeypatch.setattr(
        "cs_study_mcp.runner.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("Codex must not start"),
    )
    with pytest.raises(WorkflowError, match="approval_required"):
        run_role(service.root, "notion_writer", job_id, "합성 테스트 발행")
    assert service.get_study(job_id)["publication"] is None


def test_check_schema_recursively_requires_all_object_properties():
    def visit(value):
        if isinstance(value, dict):
            if "properties" in value:
                assert set(value["required"]) == set(value["properties"])
                assert value["additionalProperties"] is False
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(CHECK_SCHEMA)
    assert "presentation_support" in CHECK_SCHEMA["required"]


@pytest.mark.parametrize("role", ["research", "foundation", "advanced"])
def test_presentation_diagnostic_rejects_other_roles_before_process(service, monkeypatch, role):
    monkeypatch.setattr(
        "cs_study_mcp.runner.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("Codex must not start"),
    )
    with pytest.raises(ValueError, match="notion_writer만"):
        run_role(service.root, role, "unused-job", inspect_presentation=True)


def test_presentation_diagnostic_forces_check_and_never_uses_mutating_task(service, monkeypatch):
    configure(service.root)
    job_id = service.create_study("표현 읽기 진단 fixture")["job_id"]
    before = service.get_study(job_id)
    captured = {}

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            captured["command"] = command
            output = Path(command[command.index("-o") + 1])
            schema = Path(command[command.index("--output-schema") + 1])
            assert json.loads(schema.read_text(encoding="utf-8")) == CHECK_SCHEMA
            output.write_text(
                json.dumps(
                    {
                        "runtime_role": "notion_writer",
                        "project": str(service.root),
                        "visible_local_tools": ["get_runtime_info", "get_study"],
                        "get_study_ok": True,
                        "web_available": False,
                        "web_check_ok": False,
                        "notion_write_available": False,
                        "notion_read_available": True,
                        "errors": [],
                        "presentation_support": {
                            name: "모의 읽기 진단: 실제 쓰기와 표시 검증 미실시"
                            for name in CHECK_SCHEMA["properties"]["presentation_support"][
                                "properties"
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

        def communicate(self, input):
            captured["prompt"] = input

    monkeypatch.setattr("cs_study_mcp.runner.shutil.which", lambda _: "codex.exe")
    monkeypatch.setattr("cs_study_mcp.runner.subprocess.Popen", Process)
    result = run_role(
        service.root,
        "notion_writer",
        job_id,
        "그림 업로드와 페이지 생성을 실행해",
        inspect_presentation=True,
    )
    assert result["check_only"] is True
    assert result["inspect_presentation"] is True
    assert result["diagnostic_passed"] is True
    assert service.get_study(job_id) == before
    assert not (service.root / ".cs-study/assets").exists()
    prompt = captured["prompt"]
    assert "그림 업로드와 페이지 생성을 실행해" not in prompt
    assert "Notion은 호출하지 마세요." not in prompt
    assert "notion-get-tool-access" in prompt and "notion-fetch" in prompt
    assert "문서 생성·변경·업로드는 절대 수행하지 마세요." in prompt
    enabled = overrides(captured["command"])["mcp_servers"]
    assert set(enabled["cs_study"]["enabled_tools"]) == {"get_runtime_info", "get_study"}
    assert set(enabled["notion"]["enabled_tools"]) == {"notion-fetch", "notion-get-tool-access"}


def test_cli_report_survives_redirected_windows_encoding(service, monkeypatch):
    import io
    import sys

    from cs_study_mcp.cli import main

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp949")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "argv", ["cs-study", "--project", str(service.root), "list"])
    monkeypatch.setattr(
        "cs_study_mcp.cli.StudyService.list_studies", lambda _: [{"topic": "공유 — 실행 🧵"}]
    )
    main()
    stream.flush()
    assert "공유 — 실행 🧵" in buffer.getvalue().decode("utf-8")


def test_cli_presentation_flag_dispatches_to_readonly_runner(service, monkeypatch, capsys):
    from cs_study_mcp.cli import main

    captured = {}

    def run_stub(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return {"check_only": True}

    monkeypatch.setattr("cs_study_mcp.runner.run_role", run_stub)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cs-study",
            "--project",
            str(service.root),
            "run-role",
            "notion_writer",
            "--job-id",
            "fixture-job",
            "--check-presentation",
        ],
    )
    main()
    assert captured["args"][1:3] == ("notion_writer", "fixture-job")
    assert captured["kwargs"]["inspect_presentation"] is True
    assert json.loads(capsys.readouterr().out)["check_only"] is True
