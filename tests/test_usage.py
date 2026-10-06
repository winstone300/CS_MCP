import base64
import io
import json
import os
import re
import runpy
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cs_study_mcp import runner, usage
from cs_study_mcp.usage_hook import record_hook, usage_hooks


def events(path, value=None):
    value = value if value is not None else {
        "input_tokens": 100, "cached_input_tokens": 80,
        "output_tokens": 20, "reasoning_output_tokens": 7,
    }
    path.write_text("\n".join(json.dumps(item) for item in [
        {"type": "thread.started", "thread_id": "session"},
        {"type": "turn.started"}, {"type": "turn.completed", "usage": value},
    ]) + "\n", encoding="utf-8")


def make_run(root, run_id="run-1", role="foundation", **extra):
    directory = usage.run_directory(root, run_id)
    directory.mkdir(parents=True)
    metadata = {
        "run_id": run_id, "job_id": "job-1", "role": role,
        "status": "finished", "exit_code": 0, "check_only": False,
        "started_at": "2026-10-05T00:00:00+00:00",
        "finished_at": "2026-10-05T01:00:00+00:00", "hooks_expected": True,
        **extra,
    }
    usage.atomic_json(directory / "run.json", metadata)
    events(directory / "events.jsonl")
    return directory, metadata


def test_usage_totals_and_optional_fields(tmp_path):
    path = tmp_path / "events.jsonl"
    events(path)
    parsed = usage.parse_events(path)
    assert parsed["usage_status"] == "complete"
    assert parsed["tokens"]["total_tokens"] == 120
    events(path, {"input_tokens": 100, "output_tokens": 0})
    parsed = usage.parse_events(path)
    assert parsed["usage_status"] == "complete"
    assert parsed["tokens"]["cached_input_tokens"] is None
    assert parsed["tokens"]["reasoning_output_tokens"] is None
    assert parsed["tokens"]["total_tokens"] == 100


@pytest.mark.parametrize("value", [-1, True, "100", 1.5])
def test_invalid_usage_never_becomes_a_confirmed_number(tmp_path, value):
    path = tmp_path / "events.jsonl"
    events(path, {"input_tokens": value, "output_tokens": 20})
    parsed = usage.parse_events(path)
    assert parsed["usage_status"] == "partial"
    assert parsed["tokens"]["input_tokens"] is None
    assert parsed["tokens"]["total_tokens"] is None


def test_duplicate_or_multiple_turns_are_not_summed(tmp_path):
    path = tmp_path / "events.jsonl"
    events(path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 100, "output_tokens": 20,
        }}) + "\n")
    parsed = usage.parse_events(path)
    assert parsed["usage_status"] == "unknown"
    assert parsed["tokens"]["total_tokens"] is None
    events(path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"type":"turn.started"}\n')
    assert usage.parse_events(path)["usage_status"] == "unknown"


def test_torn_tail_retains_partial_evidence(tmp_path):
    path = tmp_path / "events.jsonl"
    events(path)
    with path.open("ab") as stream:
        stream.write(b'{"type":\xff')
    parsed = usage.parse_events(path)
    assert parsed["usage_status"] == "partial"
    assert parsed["tokens"]["total_tokens"] == 120
    assert "jsonl_invalid_line:4" in parsed["collection_errors"]
    assert parsed["events_bytes"] == path.stat().st_size


def test_missing_final_usage_is_unknown_not_zero(tmp_path):
    parsed = usage.parse_events(tmp_path / "missing.jsonl")
    assert parsed["usage_status"] == "unknown"
    assert all(value is None for value in parsed["tokens"].values())


def test_hook_missing_does_not_invalidate_usage_and_models_are_observed(tmp_path):
    directory, metadata = make_run(tmp_path)
    record = usage.rebuild_run(tmp_path, "run-1")
    assert record["usage_status"] == "complete"
    assert record["hook_status"] == "missing"
    assert record["observed_model"] is None
    metadata["status"] = "running"
    usage.atomic_json(directory / "run.json", metadata)
    environ = {"CS_STUDY_RUN_ID": "run-1", "CS_STUDY_JOB_ID": "job-1", "CS_STUDY_ROLE": "foundation"}
    path = record_hook(tmp_path, {
        "hook_event_name": "SessionStart", "session_id": "session", "cwd": str(tmp_path),
        "model": "observed-model", "last_assistant_message": "must not be saved",
    }, environ)
    assert path is not None
    assert "last_assistant_message" not in usage.read_json(path)
    metadata["status"] = "finished"
    usage.atomic_json(directory / "run.json", metadata)
    record = usage.rebuild_run(tmp_path, "run-1")
    assert record["observed_model"] == "observed-model"
    assert record["hook_status"] == "observed"


def test_main_and_mismatched_hook_context_produce_no_receipt(tmp_path):
    assert record_hook(tmp_path, {}, {}) is None
    directory, _ = make_run(tmp_path, status="running")
    env = {"CS_STUDY_RUN_ID": "run-1", "CS_STUDY_JOB_ID": "job-1", "CS_STUDY_ROLE": "advanced"}
    assert record_hook(tmp_path, {
        "hook_event_name": "Stop", "session_id": "session", "cwd": str(tmp_path),
    }, env) is None
    assert not (directory / "hooks").exists()
    with pytest.raises(ValueError):
        record_hook(tmp_path, {}, {"CS_STUDY_RUN_ID": "../escape"})


def test_rebuild_preserves_metadata_and_excludes_partial_and_diagnostics(tmp_path):
    directory, _ = make_run(tmp_path, status="failed", exit_code=1)
    original = (directory / "run.json").read_bytes()
    running, _ = make_run(tmp_path, "run-2", status="running", finished_at=None, exit_code=None)
    make_run(tmp_path, "run-3", check_only=True)
    unknown, _ = make_run(tmp_path, "run-4")
    (unknown / "events.jsonl").unlink()
    first = usage.rebuild_job(tmp_path, "job-1").read_text(encoding="utf-8")
    second = usage.rebuild_job(tmp_path, "job-1").read_text(encoding="utf-8")
    assert re.sub(r"집계 시각:.*", "", first) == re.sub(r"집계 시각:.*", "", second)
    assert "| foundation | 3 | 1 | 1 | 1 | 1 | 0 | 1 | 100 | 80 | 20 | 7 | 120 | 100 | 20 |" in first
    assert "| foundation | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 100 | 80 | 20 | 7 | 120 | 0 | 0 |" in first
    assert (directory / "run.json").read_bytes() == original
    assert usage.read_json(running / "run.json")["status"] == "running"
    assert usage.read_json(running / "usage.json")["provisional"] is True
    assert "09:00:00 KST" in (directory / "usage.md").read_text(encoding="utf-8")


def test_reports_are_utf8_and_confined_to_project(tmp_path):
    root = tmp_path / "공백 있는 프로젝트"
    make_run(root)
    usage.rebuild_run(root, "run-1")
    assert "토큰" in usage.usage_report(root, run_id="run-1")
    for unsafe in ("../escape", "/absolute", "a/b", "a\\b", ".."):
        with pytest.raises(ValueError):
            usage.usage_report(root, run_id=unsafe, rebuild=True)


def test_concurrent_finish_and_rebuild_are_idempotent(tmp_path):
    for number, role in enumerate(("research", "foundation", "advanced")):
        make_run(tmp_path, f"run-{number}", role)

    def finish(number):
        usage.rebuild_run(tmp_path, f"run-{number}")
        usage.rebuild_job(tmp_path, "job-1", refresh_runs=False)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(finish, i) for i in range(3)]
        futures.append(pool.submit(usage.rebuild_job, tmp_path, "job-1"))
        for future in futures:
            future.result()
    report = usage.usage_report(tmp_path, job_id="job-1", rebuild=True)
    assert report.count("| 100 | 80 | 20 | 7 | 120 |") == 3
    assert "집계 오류: 없음" in report


def test_atomic_replace_failure_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    path.write_text("original", encoding="utf-8")
    monkeypatch.setattr(usage.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        usage.atomic_write(path, "new")
    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob("*.tmp")) == []


def test_windows_transient_sharing_error_is_retried(tmp_path, monkeypatch):
    original_replace = usage.os.replace
    calls = []

    def replace(source, target):
        calls.append(target)
        if len(calls) == 1:
            raise PermissionError("sharing violation")
        original_replace(source, target)

    monkeypatch.setattr(usage.os, "replace", replace)
    path = tmp_path / "run.json"
    usage.atomic_json(path, {"status": "finished"})
    assert len(calls) == 2
    assert usage.read_json(path)["status"] == "finished"


def test_hook_command_writes_receipt_without_model_context(tmp_path):
    directory, _ = make_run(tmp_path, status="running")
    environment = {**os.environ, "CS_STUDY_RUN_ID": "run-1", "CS_STUDY_JOB_ID": "job-1", "CS_STUDY_ROLE": "foundation"}
    payload = {"hook_event_name": "Stop", "session_id": "session", "cwd": str(tmp_path), "model": "reported-model"}
    process = subprocess.run([sys.executable, "-m", "cs_study_mcp.usage_hook", str(tmp_path)],
                             input=json.dumps(payload).encode(), capture_output=True, env=environment,
                             timeout=5, check=False)
    assert process.returncode == 0
    assert json.loads(process.stdout) == {}
    assert process.stderr == b""
    receipt = usage.read_json(next((directory / "hooks").glob("*.json")))
    assert receipt["event"] == "Stop"
    assert receipt["model"] == "reported-model"


def test_lock_timeout_and_release_after_process_termination(tmp_path):
    path = tmp_path / "usage.lock"
    with usage.report_lock(path):
        with pytest.raises(TimeoutError), usage.report_lock(path, timeout=0.05):
            pass
    code = (
        "import sys,time; from pathlib import Path; from cs_study_mcp.usage import report_lock; "
        "\nwith report_lock(Path(sys.argv[1])): print('ready',flush=True); time.sleep(30)"
    )
    process = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE)
    try:
        assert process.stdout.readline().strip() == b"ready"
    finally:
        process.terminate()
        process.wait(timeout=5)
        process.stdout.close()
    with usage.report_lock(path, timeout=0.2):
        pass


def test_capture_keeps_draining_both_streams_after_disk_failure(tmp_path, monkeypatch):
    original_open = Path.open

    def failing_open(path, *args, **kwargs):
        if path.name == "events.jsonl":
            raise OSError("disk full")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    # Enough output to fill both pipes if either reader stops after the disk error.
    code = "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x'*200000); sys.stderr.buffer.write(b'y'*200000)"
    process = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    errors = []
    runner.capture_process(process, "작업", tmp_path, errors)
    assert process.returncode == 0
    assert (tmp_path / "execution.log").stat().st_size == 200000
    assert any("stream_open_failed:events.jsonl" in item for item in errors)


def test_finalize_errors_never_replace_original_worker_failure(tmp_path, monkeypatch):
    directory, metadata = make_run(tmp_path, status="failed", exit_code=19)
    metadata["collection_errors"] = []

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(runner, "atomic_json", fail)
    monkeypatch.setattr(runner, "rebuild_run", fail)
    monkeypatch.setattr(runner, "rebuild_job", fail)
    runner.finalize_usage(tmp_path, directory, metadata)
    assert metadata["status"] == "failed"
    assert metadata["exit_code"] == 19
    assert metadata["usage_report_status"] == "failed"


def test_hook_config_preserves_user_hooks_and_is_idempotent(tmp_path):
    generator = runpy.run_path(str(Path(__file__).parents[1] / "scripts/configure_codex.py"))
    generator["configure"](tmp_path)
    path = tmp_path / ".codex/config.toml"
    assert "hooks" not in tomllib.loads(path.read_text(encoding="utf-8"))
    assert tomllib.loads(path.read_text(encoding="utf-8"))["features"]["hooks"] is False
    legacy = (
        '\n# BEGIN CS-STUDY MANAGED USAGE HOOKS\n'
        '[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "command"\n'
        'command = "old-usage-hook"\ntimeout = 3\n'
        '# END CS-STUDY MANAGED USAGE HOOKS\n'
    )
    custom = '\n# 사용자 훅\n[[hooks.Stop]]\nmatcher = "custom"\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "user-command"\ntimeout = 2\n'
    path.write_text(path.read_text(encoding="utf-8") + legacy + custom, encoding="utf-8")
    separate = tmp_path / ".codex/hooks.json"
    separate.write_text('{"hooks": {"Stop": []}}', encoding="utf-8")
    generator["configure"](tmp_path)
    first = path.read_text(encoding="utf-8")
    generator["configure"](tmp_path)
    assert path.read_text(encoding="utf-8") == first
    assert custom in first
    assert separate.read_text(encoding="utf-8") == '{"hooks": {"Stop": []}}'
    config = tomllib.loads(first)
    assert config["hooks"] == {"Stop": [{"matcher": "custom", "hooks": [{
        "type": "command", "command": "user-command", "timeout": 2,
    }]}]}
    assert "MANAGED USAGE HOOKS" not in first
    assert "old-usage-hook" not in first
    assert config["features"]["hooks"] is False


@pytest.mark.parametrize("flags", [
    '[features]\napps = false\nhooks = true # 기존 값\n',
    '[features]\napps = false\n',
    'features.apps = false\nfeatures.hooks = true # 기존 값\n',
    'features.apps = false\n',
])
def test_project_hooks_disabled_preserving_other_feature_settings(tmp_path, flags):
    generator = runpy.run_path(str(Path(__file__).parents[1] / "scripts/configure_codex.py"))
    path = tmp_path / ".codex/config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(flags + generator["managed_block"](tmp_path, "main") + '\n', encoding="utf-8")
    generator["configure"](tmp_path)
    first = path.read_text(encoding="utf-8")
    assert tomllib.loads(first)["features"] == {"apps": False, "hooks": False}
    if "# 기존 값" in flags:
        assert "# 기존 값" in first
    generator["configure"](tmp_path)
    assert path.read_text(encoding="utf-8") == first


def test_run_role_hook_definitions_keep_commands_and_timeouts(tmp_path):
    config = usage_hooks(tmp_path)
    for event in usage.HOOK_EVENTS:
        hook = config[event][0]["hooks"][0]
        assert hook["timeout"] == 3
        assert "CS_STUDY_RUN_ID" not in hook["command"]
        decoded = base64.b64decode(hook["command_windows"].split()[-1]).decode("utf-16-le")
        assert "usage_hook" in decoded
        assert str(tmp_path.as_posix()) in decoded


def test_cli_usage_rebuild_never_initializes_study_service(tmp_path, monkeypatch, capsys):
    from cs_study_mcp import cli

    make_run(tmp_path)
    monkeypatch.setattr(cli, "StudyService", lambda *a: pytest.fail("usage must not open study DB"))
    monkeypatch.setattr(sys, "argv", ["cs-study", "--project", str(tmp_path), "usage", "--run-id", "run-1", "--rebuild"])
    cli.main()
    assert "total_tokens | 120" in capsys.readouterr().out


def test_requested_model_records_toml_priority_and_environment_is_local(service, monkeypatch):
    script = Path(__file__).parents[1] / "scripts/configure_codex.py"
    runpy.run_path(str(script))["configure"](service.root)
    config = service.root / ".codex/agents/foundation.toml"
    config.write_text('model = "role-model"\nmodel_reasoning_effort = "high"\n' + config.read_text(encoding="utf-8"), encoding="utf-8")
    job_id = service.create_study("사용량 모의 실행")["job_id"]
    monkeypatch.setattr(runner, "codex_version", lambda _: "test-cli")
    monkeypatch.setattr(runner, "resolve_codex", lambda: "test-codex")
    captured = {}

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            captured.update(kwargs)
            captured["command"] = command
            output = Path(command[command.index("-o") + 1])
            output.write_text("역할 응답", encoding="utf-8")
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b'{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":2}}\n')
            self.stderr = io.BytesIO(b"warning\n")

        def wait(self):
            return 0

    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    before = dict(os.environ)
    result = runner.run_role(service.root, "foundation", job_id, "설명", model="parent-model", reasoning_effort="low")
    assert os.environ == before
    assert result["requested_model"] == "role-model"
    assert result["model_source"] == "role_toml"
    assert result["requested_reasoning_effort"] == "high"
    assert result["usage_status"] == "complete"
    assert captured["env"]["CS_STUDY_RUN_ID"] == result["run_id"]
    cli_settings = tomllib.loads("\n".join(
        captured["command"][i + 1] for i, arg in enumerate(captured["command"]) if arg == "-c"
    ))
    assert cli_settings["shell_environment_policy"]["set"]["CS_STUDY_RUN_ID"] == result["run_id"]
    assert cli_settings["features"]["hooks"] is True
    assert "inherit" not in cli_settings["shell_environment_policy"]
    assert usage.read_json(Path(result["usage_path"]).with_suffix(".json"))["tokens"]["total_tokens"] == 5


def test_actual_cli_0_160_usage_fixture():
    parsed = usage.parse_events(Path(__file__).parent / "fixtures/codex_0_160_usage.jsonl")
    assert parsed["usage_status"] == "complete"
    assert parsed["tokens"] == {
        "input_tokens": 98121, "cached_input_tokens": 67200,
        "output_tokens": 1020, "reasoning_output_tokens": 96, "total_tokens": 99141,
    }


def test_rebuild_warns_about_failed_individual_report(tmp_path, monkeypatch):
    make_run(tmp_path)
    usage.rebuild_run(tmp_path, "run-1")
    monkeypatch.setattr(usage, "rebuild_run", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    path = usage.rebuild_job(tmp_path, "job-1")
    assert "run_rebuild_failed:run-1:OSError" in path.read_text(encoding="utf-8")


def test_worker_startup_exception_survives_usage_save_failure(service, monkeypatch):
    runpy.run_path(str(Path(__file__).parents[1] / "scripts/configure_codex.py"))["configure"](service.root)
    job_id = service.create_study("원래 실패 보존")["job_id"]
    monkeypatch.setattr(runner, "resolve_codex", lambda: "test-codex")
    monkeypatch.setattr(runner, "codex_version", lambda _: "test-cli")

    def original_error(*args, **kwargs):
        raise OSError("original worker startup failure")

    def report_error(*args, **kwargs):
        raise OSError("usage storage failure")

    monkeypatch.setattr(runner.subprocess, "Popen", original_error)
    monkeypatch.setattr(runner, "rebuild_run", report_error)
    monkeypatch.setattr(runner, "rebuild_job", report_error)
    with pytest.raises(OSError, match="original worker startup failure"):
        runner.run_role(service.root, "foundation", job_id, "작업")
    record = next((service.root / ".cs-study/runs").glob("*/run.json"))
    assert usage.read_json(record)["status"] == "failed"


def test_interruption_is_preserved_when_terminate_times_out(tmp_path):
    class Process:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"partial event")
            self.stderr = io.BytesIO(b"worker warning")
            self.calls = 0
            self.killed = False

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt("original interruption")
            if self.calls == 2:
                raise subprocess.TimeoutExpired("worker", timeout)

        def terminate(self):
            pass

        def kill(self):
            self.killed = True

    process = Process()
    with pytest.raises(KeyboardInterrupt, match="original interruption"):
        runner.capture_process(process, "작업", tmp_path, [])
    assert process.killed


@pytest.mark.skipif(os.name != "nt", reason="Windows hook shell dispatch")
def test_windows_hook_command_survives_both_shells_and_unicode_path(tmp_path):
    root = tmp_path / "한글 공백 '따옴표'"
    directory, _ = make_run(root, status="running")
    command = usage_hooks(root)["Stop"][0]["hooks"][0]["command_windows"]
    environment = {**os.environ, "CS_STUDY_RUN_ID": "run-1", "CS_STUDY_JOB_ID": "job-1", "CS_STUDY_ROLE": "foundation", "PYTHONUTF8": "1"}
    payload = json.dumps({"hook_event_name": "Stop", "session_id": "session", "cwd": str(root)})
    for shell in (["cmd.exe", "/d", "/s", "/c"], ["powershell.exe", "-NoProfile", "-Command"]):
        result = subprocess.run([*shell, command], input=payload.encode("utf-8"),
                                capture_output=True, timeout=10, env=environment, check=False)
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        assert json.loads(result.stdout) == {}
    assert len(list((directory / "hooks").glob("*.json"))) == 2
