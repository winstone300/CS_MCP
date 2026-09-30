"""Run a role's TOML in its own Codex CLI session without copying credentials."""

import json
import os
import re
import shutil
import subprocess
import tomllib
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .render import digest
from .server import TOOLS_BY_ROLE
from .service import StudyService, WorkflowError
from .storage import now

ROLES = tuple(role for role in TOOLS_BY_ROLE if role != "main")
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "runtime_role": {"type": "string"},
        "project": {"type": "string"},
        "visible_local_tools": {"type": "array", "items": {"type": "string"}},
        "get_study_ok": {"type": "boolean"},
        "web_available": {"type": "boolean"},
        "web_check_ok": {"type": "boolean"},
        "notion_write_available": {"type": "boolean"},
        "notion_read_available": {"type": "boolean"},
        "errors": {"type": "array", "items": {"type": "string"}},
        "presentation_support": {
            "type": "object",
            "properties": {
                name: {"type": "string"}
                for name in (
                    "target_read",
                    "mermaid",
                    "table",
                    "toggle",
                    "image_upload",
                    "evidence",
                    "limitations",
                )
            },
            "required": [
                "target_read",
                "mermaid",
                "table",
                "toggle",
                "image_upload",
                "evidence",
                "limitations",
            ],
            "additionalProperties": False,
        },
    },
    "required": [
        "runtime_role",
        "project",
        "visible_local_tools",
        "get_study_ok",
        "web_available",
        "web_check_ok",
        "notion_write_available",
        "notion_read_available",
        "errors",
        "presentation_support",
    ],
    "additionalProperties": False,
}


def validate_probe(root: Path, role: str, report: dict) -> bool:
    expected = {"get_runtime_info", "get_study"} if role == "notion_writer" else TOOLS_BY_ROLE[role]
    visible = report.get("visible_local_tools", [])
    if not isinstance(visible, list) or not all(isinstance(name, str) for name in visible):
        return False
    project = report.get("project")
    return bool(
        report.get("runtime_role") == role
        and isinstance(project, str)
        and Path(project).resolve() == root.resolve()
        and {name.rsplit("__", 1)[-1] for name in visible} == expected
        and report.get("get_study_ok") is True
        and report.get("web_available") is (role == "research")
        and (role != "research" or report.get("web_check_ok") is True)
        and report.get("notion_write_available") is False
        and report.get("notion_read_available") is (role == "notion_writer")
        and report.get("errors") == []
    )


def load_role(root: Path, role: str) -> dict:
    if role not in ROLES:
        raise ValueError(f"알 수 없는 역할: {role}")
    path = root / ".codex" / "agents" / f"{role}.toml"
    config = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    server = config.get("mcp_servers", {}).get("cs_study", {})
    args = server.get("args", [])
    if (
        config.get("name") != role
        or not config.get("developer_instructions", "").strip()
        or args[-2:] != ["--role", role]
        or not server.get("enabled")
        or set(server.get("enabled_tools", [])) != TOOLS_BY_ROLE[role]
        or Path(server.get("cwd", "")).resolve() != root.resolve()
        or "--project" not in args
        or args.index("--project") + 1 >= len(args)
        or Path(args[args.index("--project") + 1]).resolve() != root.resolve()
    ):
        raise ValueError("역할 구성 불일치: setup.ps1 -SkipInstall 후 다시 확인하세요.")
    if config.get("web_search") != ("live" if role == "research" else "disabled"):
        raise ValueError("research만 web_search=live여야 합니다.")
    if config.get("mcp_servers", {}).get("notion", {}).get("enabled", False) != (
        role == "notion_writer"
    ):
        raise ValueError("notion_writer만 Notion MCP를 활성화해야 합니다.")
    return config


def config_arguments(values: dict) -> list[str]:
    """Serialize TOML scalar/array overrides into argv, never shell command strings."""
    result = []

    def visit(path: str, value):
        if isinstance(value, dict):
            for key, child in value.items():
                # Codex's CLI dotted-path parser expects bare normal keys.
                # Quoting the top-level key silently targets a different config path.
                if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
                    raise ValueError(f"CLI 설정 키에는 영문·숫자·밑줄·하이픈만 지원합니다: {key}")
                visit(f"{path}.{key}" if path else key, child)
        else:
            if value is None:
                raise ValueError("Codex 구성에는 null 값을 전달할 수 없습니다.")
            result.extend(["-c", f"{path}={json.dumps(value, ensure_ascii=False)}"])

    visit("", values)
    return result


def build_command(
    root: Path,
    role: str,
    output: Path,
    *,
    check: bool = False,
    model: str | None = None,
    reasoning_effort: str | None = None,
    codex: str = "codex",
) -> list[str]:
    config = load_role(root, role)
    overrides = {
        key: config[key] for key in ("developer_instructions", "web_search", "mcp_servers")
    }
    for key, inherited in (("model", model), ("model_reasoning_effort", reasoning_effort)):
        value = config.get(key, inherited)
        if value is not None:
            overrides[key] = value
    # A role worker must not recursively delegate or become the main agent.
    overrides["agents"] = {"enabled": False}
    if check and role == "notion_writer":
        overrides["mcp_servers"]["notion"]["enabled_tools"] = [
            "notion-fetch",
            "notion-get-tool-access",
        ]
        overrides["mcp_servers"]["cs_study"]["enabled_tools"] = ["get_runtime_info", "get_study"]
    return [
        codex,
        "exec",
        "--ephemeral",
        "-C",
        str(root),
        *config_arguments(overrides),
        "-o",
        str(output),
        "-",
    ]


def check_publication_approval(study: dict) -> None:
    draft = study.get("draft")
    latest = study["reviews"][-1] if study.get("reviews") else None
    publication = study.get("publication")
    parent = publication["parent_page_id"] if publication else study["configured_parent_page_id"]
    if (
        not draft
        or not latest
        or latest["decision"] != "approve"
        or latest["draft_version"] != draft["version"]
        or latest["draft_hash"] != draft["content_hash"]
        or (draft.get("bundle_hash") and latest.get("bundle_hash") != draft["bundle_hash"])
        or not parent
        or latest["parent_page_id"] != parent
        or (not publication and study["status"] != "approved")
    ):
        raise WorkflowError(
            "approval_required", "승인된 최신 초안과 대상이 있어야 writer를 실행할 수 있습니다."
        )


@contextmanager
def worker_slot(root: Path):
    """Three OS file locks, released even on a wrapper crash; no polling queue."""
    directory = root / ".cs-study" / "runner-slots"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        file = (directory / f"{index}.lock").open("a+b")
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            continue
        try:
            yield
        finally:
            if os.name == "nt":
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
            file.close()
        return
    raise WorkflowError(
        "workers_busy", "이미 역할 실행 3개가 진행 중입니다. 완료 후 다시 실행하세요."
    )


def run_role(
    root: Path,
    role: str,
    job_id: str,
    task: str = "",
    *,
    check: bool = False,
    model: str | None = None,
    reasoning_effort: str | None = None,
    inspect_presentation: bool = False,
) -> dict:
    if inspect_presentation:
        if role != "notion_writer":
            raise ValueError("표현 형식 읽기 진단은 notion_writer만 수행합니다.")
        check = True
    root = root.resolve()
    load_role(root, role)
    study = StudyService(root).get_study(job_id)
    if role == "notion_writer" and not check:
        check_publication_approval(study)
        StudyService(root).validate_publication_bundle(job_id)
    if not check and not task.strip():
        raise ValueError("담당 작업을 --task 또는 --task-file로 지정하세요.")
    codex = shutil.which("codex")
    if not codex:
        raise ValueError("PATH에서 Codex CLI를 찾을 수 없습니다.")
    run_id = str(uuid4())
    directory = root / ".cs-study" / "runs" / run_id
    directory.mkdir(parents=True)
    output = directory / "result.md"
    command = build_command(
        root, role, output, check=check, model=model, reasoning_effort=reasoning_effort, codex=codex
    )
    if check:
        schema_path = directory / "check-schema.json"
        schema_path.write_text(json.dumps(CHECK_SCHEMA), encoding="utf-8")
        command[-1:-1] = ["--output-schema", str(schema_path)]
    prompt = (
        f"당신은 메인이 명시적으로 위임한 {role} 역할의 독립 Codex CLI 작업자입니다.\n"
        f"기존 job_id={job_id}만 사용하세요. 새 학습 작업을 생성하지 마세요.\n"
        f"먼저 get_runtime_info를 호출해 실제 role={role}와 프로젝트 경로를 확인하세요. "
        "불일치하거나 도구가 없으면 다른 도구·셸로 우회하지 말고 즉시 보고하세요.\n"
        "get_study로 작업 시작 시 고정된 rules_text/rules_hash와 최신 산출물을 읽으세요.\n"
        "현재 사용자가 승인한 실행 방식은 역할 TOML을 직접 적용한 독립 CLI입니다. "
        "저장된 규칙의 내장 subagent 호출 절차는 메인이 이 CLI 호출로 수행합니다. "
        "새 에이전트를 실행하지 말고 자신의 역할만 수행하세요. "
        "그 밖의 고정된 품질·검토·발행 승인 규칙은 그대로 준수하세요.\n"
    )
    if check:
        prompt += (
            "이번 작업은 읽기 전용 연결 진단입니다. 자료·문서·검토·승인·발행을 저장하지 마세요. "
            "실제로 노출된 함수 목록과 ALL_TOOLS/호출 가능한 tools 바인딩에서 역할별 필수 도구를 확인하고 main의 create_study, "
            "save_draft, record_review가 없는지도 확인하세요. get_runtime_info/get_study 외의 "
            "로컬 MCP 도구는 호출하지 마세요. Notion은 호출하지 마세요. "
            "research라면 내장 웹 도구로 https://docs.python.org/3/library/threading.html 한 페이지만 "
            "열어 웹 연결을 검증하세요. 다른 역할에는 웹 도구가 없는지 확인하세요. "
            "writer 진단은 로컬 조회 도구와 Notion 읽기 도구만 허용된 상태입니다. "
            "최종 결과는 지정된 JSON 스키마로 보고하세요. runtime_role/project는 실제 get_runtime_info 결과, "
            "visible_local_tools는 실제 호출 가능한 cs_study 도구의 접두사 없는 이름 목록입니다. "
            "web_available은 내장 web 도구 노출 여부, web_check_ok는 실제 웹 열기 성공 여부입니다. "
            "Notion 쓰기 도구 노출 여부도 기록하세요. 불일치·실패는 errors에 기록하고 성공을 추정하지 마세요."
            "notion_read_available은 실제 Notion fetch 도구 바인딩/카탈로그의 존재로 판단하세요. "
            "writer 진단에서 get_runtime_info가 반환한 서버 전체 tools와 읽기 전용으로 필터된 실제 노출 목록의 "
            "차이는 의도된 것이므로 오류로 기록하지 마세요."
            "추가 표현 형식 진단이 아닌 경우 presentation_support의 모든 값은 미실시로 기록하세요."
        )
        if inspect_presentation:
            prompt = prompt.replace(
                "Notion은 호출하지 마세요.",
                "이번 표현 형식 진단에서는 아래 명시한 Notion 읽기만 허용합니다.",
            )
            prompt += (
                "\n추가 읽기 전용 표현 형식 진단: notion-get-tool-access의 실제 접근/도구 정보와 "
                "notion-fetch로 get_study.configured_parent_page_id 상위 페이지 조회를 확인하세요. "
                "Notion MCP가 제공하는 Markdown 규격 resource가 있으면 목록에서 찾아 읽으세요. "
                "문서 생성·변경·업로드는 절대 수행하지 마세요. "
                "presentation_support에 target_read, mermaid, table, toggle, image_upload, evidence, limitations를 문자열로 기록하세요. "
                "도구 설명/읽은 규격이 명시하는 작성 형식과 실제 반환 형식만 보고하고 근거 출처를 evidence에 적으세요. "
                "특히 evidence에 읽은 규격의 table 전체 예시(헤더 속성·colgroup 포함), details 전체 예시(탭 들여쓰기를 보존), 이미지 참조 예시를 짧게 원문 인용하세요. "
                "원문을 찾지 못한 기능은 unknown으로, 재조회/실제 표시 검증은 미실시로 표시하세요. "
                "사용자 데이터를 결과에 길게 복사하지 마세요. 이 보고는 실제 쓰기 호환성 통과를 뜻하지 않습니다."
            )
    else:
        prompt += "\n메인이 위임한 작업:\n" + task.strip()
    metadata = {
        "run_id": run_id,
        "job_id": job_id,
        "role": role,
        "check_only": check,
        "inspect_presentation": inspect_presentation,
        "status": "running",
        "started_at": now(),
        "result_path": str(output),
        "config_hash": digest(
            (root / ".codex/agents" / f"{role}.toml").read_text(encoding="utf-8-sig")
        ),
    }
    with worker_slot(root):
        meta_path = directory / "run.json"
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"역할 실행 {run_id}: {directory}", flush=True)
        try:
            with (directory / "execution.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    cwd=root,
                )
                try:
                    process.communicate(input=prompt)
                except BaseException:
                    process.terminate()
                    process.wait(timeout=10)
                    raise
            metadata["exit_code"] = process.returncode
            # Process completion is not study completion or semantic validation.
            metadata["status"] = (
                "finished" if process.returncode == 0 and output.is_file() else "failed"
            )
            if check and metadata["status"] == "finished":
                try:
                    report = json.loads(output.read_text(encoding="utf-8"))
                except ValueError:
                    report = {}
                metadata["diagnostic_passed"] = isinstance(report, dict) and validate_probe(
                    root, role, report
                )
                if not metadata["diagnostic_passed"]:
                    metadata["status"] = "failed"
        except BaseException as exc:
            metadata["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            raise
        finally:
            metadata["finished_at"] = now()
            meta_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    if metadata["status"] != "finished":
        raise WorkflowError(
            "worker_failed", f"실행 로그를 확인하세요: {directory / 'execution.log'}"
        )
    return {**metadata, "message": output.read_text(encoding="utf-8")}
