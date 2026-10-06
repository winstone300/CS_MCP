"""Small command hook: metadata only, no model context or workflow operations."""

import base64
import json
import os
import shlex
import sys
from pathlib import Path
from uuid import uuid4

from .usage import HOOK_EVENTS, ROLES, atomic_json, read_json, run_directory, timestamp


def usage_hooks(root: Path) -> dict:
    """Build session-only hooks for the run-role child, never project config."""
    python = Path(sys.executable).resolve().as_posix()
    command = f"{shlex.quote(python)} -m cs_study_mcp.usage_hook {shlex.quote(root.as_posix())}"
    # Explicit PowerShell invocation survives cmd and PowerShell dispatch alike.
    windows_script = (
        "& '" + python.replace("'", "''") + "' -m cs_study_mcp.usage_hook '"
        + root.as_posix().replace("'", "''") + "'"
    )
    encoded = base64.b64encode(windows_script.encode("utf-16-le")).decode("ascii")
    handler = {
        "type": "command", "command": command,
        "command_windows": f"powershell.exe -NoProfile -NonInteractive -EncodedCommand {encoded}",
        "timeout": 3,
    }
    return {event: [{"hooks": [dict(handler)]}] for event in HOOK_EVENTS}


def record_hook(root: Path, payload: dict, environ: dict) -> Path | None:
    run_id = environ.get("CS_STUDY_RUN_ID")
    if not run_id:
        return None
    directory = run_directory(root, run_id)
    metadata = read_json(directory / "run.json")
    if (
        metadata.get("run_id") != run_id
        or metadata.get("job_id") != environ.get("CS_STUDY_JOB_ID")
        or metadata.get("role") != environ.get("CS_STUDY_ROLE")
        or metadata.get("role") not in ROLES
        or metadata.get("status") != "running"
        or payload.get("hook_event_name") not in HOOK_EVENTS
        or not isinstance(payload.get("session_id"), str)
        or not payload.get("session_id")
        or Path(payload.get("cwd", "")).resolve() != root.resolve()
    ):
        return None
    if (directory / "hooks").resolve().parent != directory.resolve():
        raise ValueError("훅 저장 경로가 실행 디렉터리를 벗어났습니다.")
    receipt = {
        **{key: metadata[key] for key in ("run_id", "job_id", "role")},
        "session_id": payload["session_id"],
        "event": payload["hook_event_name"],
        "timestamp": timestamp(),
        "model": payload.get("model") if isinstance(payload.get("model"), str) else None,
        "turn_id": payload.get("turn_id") if isinstance(payload.get("turn_id"), str) else None,
    }
    target = directory / "hooks" / f"{uuid4().hex}.json"
    atomic_json(target, receipt)
    return target


def main() -> None:
    try:
        if len(sys.argv) != 2:
            raise ValueError("프로젝트 경로가 필요합니다.")
        # Main sessions have no run context: do not even read their hook payload.
        if os.environ.get("CS_STUDY_RUN_ID"):
            payload = json.loads(sys.stdin.read())
            if not isinstance(payload, dict):
                raise ValueError("훅 입력이 객체가 아닙니다.")
            record_hook(Path(sys.argv[1]).resolve(), payload, os.environ)
    except Exception as exc:
        print(f"cs-study usage hook: {type(exc).__name__}: {exc}", file=sys.stderr)
    # Stop requires JSON; this empty object contains no instructions or control decisions.
    print("{}")


if __name__ == "__main__":
    main()
