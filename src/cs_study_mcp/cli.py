import argparse
import importlib.metadata
import json
import shutil
import sys
import tomllib
from pathlib import Path

from .server import TOOLS_BY_ROLE, create_server
from .service import StudyService
from .settings import configured_parent, notion_id


def doctor(root: Path) -> dict:
    checks: dict = {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "mcp": importlib.metadata.version("mcp"),
        "pydantic": importlib.metadata.version("pydantic"),
        "rules_present": (root / "AGENTS.md").is_file(),
        "codex_on_path": shutil.which("codex") is not None,
        "notion_parent_page_id": configured_parent(root),
        "network_checked": False,
        "notion_oauth_checked": False,
        "codex_role_runtime_checked": False,
        "execution_mode": "independent_codex_cli",
    }
    configs = {}
    for path in [
        root / ".codex" / "config.toml",
        *sorted((root / ".codex" / "agents").glob("*.toml")),
    ]:
        if path.is_file():
            with path.open("rb") as file:
                config = tomllib.load(file)
            role = config.get("name", "main")
            server = config.get("mcp_servers", {}).get("cs_study", {})
            notion = config.get("mcp_servers", {}).get("notion", {})
            expected = TOOLS_BY_ROLE.get(role, set())
            exposed = set(server.get("enabled_tools", []))
            arguments = server.get("args", [])
            configs[role] = {
                "tools_match": exposed == expected,
                "server_role_matches": arguments[-2:] == ["--role", role],
                "local_command_exists": Path(server.get("command", "")).is_file(),
                "notion_enabled": notion.get("enabled", False),
            }
    checks["roles"] = configs
    checks["local_ready"] = (
        checks["rules_present"]
        and set(configs) == set(TOOLS_BY_ROLE)
        and all(
            item["tools_match"] and item["server_role_matches"] and item["local_command_exists"]
            for item in configs.values()
        )
        and all(
            not item["notion_enabled"] for role, item in configs.items() if role != "notion_writer"
        )
        and configs.get("notion_writer", {}).get("notion_enabled", False)
    )
    checks["live_setup_required"] = [
        "Codex에서 프로젝트 신뢰 및 메인 cs_study 연결 확인",
        "run-role <역할> --job-id <ID> --check로 실제 역할·도구 검사",
        "Codex ChatGPT 로그인·웹 검색 사용 가능 확인",
        "README의 Notion OAuth 명령 완료 후 writer 역할의 실제 도구 목록 확인",
        "초안 사용자 승인 후 Notion 생성·재조회 시험",
    ]
    return checks


def main() -> None:
    # Redirected Windows streams otherwise default to cp949 and can fail after
    # a successful operation when a role report contains characters such as —.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Codex용 로컬 CS 스터디 MCP")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="stdio MCP 실행(stdout은 프로토콜 전용)")
    serve.add_argument("--role", choices=sorted(TOOLS_BY_ROLE), default="main")
    configure = sub.add_parser("configure", help="발행 대상 설정(네트워크 요청 없음)")
    configure.add_argument("--notion-parent", required=True)
    sub.add_parser("doctor", help="로컬 구성 검사(OAuth·외부 연결은 별도 확인)")
    sub.add_parser("list", help="저장된 작업 목록")
    show = sub.add_parser("show", help="작업 상태와 산출물")
    show.add_argument("job_id")
    export = sub.add_parser("export", help="DB에 저장된 최신 초안을 Markdown으로 다시 내보내기")
    export.add_argument("job_id")
    worker = sub.add_parser("run-role", help="역할 TOML을 직접 적용한 독립 Codex CLI 실행")
    worker.add_argument("role", choices=["research", "foundation", "advanced", "notion_writer"])
    worker.add_argument("--job-id", required=True)
    task = worker.add_mutually_exclusive_group()
    task.add_argument("--task", default="")
    task.add_argument("--task-file", type=Path)
    worker.add_argument("--check", action="store_true", help="쓰기 없이 실제 역할 도구 연결 검사")
    worker.add_argument(
        "--check-presentation",
        action="store_true",
        help="writer의 읽기 도구로 대상·표현 형식 규격 확인(쓰기 금지)",
    )
    worker.add_argument("--model", help="메인의 모델 전달. 역할 TOML 명시값이 우선")
    worker.add_argument(
        "--reasoning-effort",
        choices=["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
    )
    args = parser.parse_args()
    root = args.project.resolve()
    try:
        if args.command == "serve":
            create_server(root, args.role).run(transport="stdio")
            return
        if args.command == "configure":
            parent = notion_id(args.notion_parent)
            root.mkdir(parents=True, exist_ok=True)
            (root / "study.local.toml").write_text(
                f'[notion]\nparent_page_id = "{parent}"\n', encoding="utf-8"
            )
            result = {"parent_page_id": parent, "config": str(root / "study.local.toml")}
        elif args.command == "doctor":
            result = doctor(root)
        elif args.command == "run-role":
            from .runner import run_role

            task_text = (
                args.task_file.read_text(encoding="utf-8-sig") if args.task_file else args.task
            )
            result = run_role(
                root,
                args.role,
                args.job_id,
                task_text,
                check=args.check,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                inspect_presentation=args.check_presentation,
            )
        else:
            service = StudyService(root)
            if args.command == "list":
                result = service.list_studies()
            elif args.command == "show":
                result = service.get_study(args.job_id)
            else:
                path = service.export_draft(args.job_id)
                result = {"path": str(path), "preview_path": str(path)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "doctor" and not result["local_ready"]:
            raise SystemExit(1)
    except (ValueError, OSError, tomllib.TOMLDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
