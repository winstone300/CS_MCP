"""Deterministic, local usage reports. Raw CLI events remain the source of truth."""

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROLES = ("research", "foundation", "advanced", "notion_writer")
FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
HOOK_EVENTS = ("SessionStart", "Stop", "SessionEnd", "Interrupt")
SCHEMA_VERSION = 1
PARSER_VERSION = "codex-exec-single-turn-v1"
KST = timezone(timedelta(hours=9))


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("실행/작업 ID에는 영문·숫자·밑줄·하이픈만 허용합니다.")
    return value


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers can briefly hold a handle without FILE_SHARE_DELETE.
        deadline = time.monotonic() + 1
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def report_lock(path: Path, timeout: float = 3):
    """OS lock: no stale-owner cleanup is necessary after a killed process."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"보고서 잠금 시간 초과: {path}") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_json(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError(f"JSON 객체가 아닙니다: {path}")
    return result


def run_directory(root: Path, run_id: str) -> Path:
    parent = (root / ".cs-study/runs").resolve()
    directory = parent / safe_id(run_id)
    if directory.resolve().parent != parent:
        raise ValueError("프로젝트 실행 디렉터리를 벗어난 경로입니다.")
    return directory


def parse_events(path: Path) -> dict:
    """Read one bounded snapshot; never infer tokens from text or tool calls."""
    errors = []
    usages = []
    threads = []
    started = 0
    cutoff = 0
    damaged = False
    try:
        with path.open("rb") as stream:
            cutoff = os.fstat(stream.fileno()).st_size
            remaining = cutoff
            number = 0
            while remaining:
                raw = stream.readline(remaining)
                if not raw:
                    break
                remaining -= len(raw)
                number += 1
                try:
                    event = json.loads(raw)
                    if not isinstance(event, dict):
                        raise ValueError("event is not an object")
                except (ValueError, UnicodeError):
                    errors.append(f"jsonl_invalid_line:{number}")
                    damaged = True
                    continue
                kind = event.get("type")
                if kind == "thread.started" and isinstance(event.get("thread_id"), str):
                    threads.append(event["thread_id"])
                elif kind == "turn.started":
                    started += 1
                elif kind == "turn.completed":
                    usages.append((number, event.get("usage")))
    except OSError as exc:
        errors.append(f"events_unavailable:{type(exc).__name__}")
        damaged = True
    tokens = dict.fromkeys(FIELDS)
    evidence_line = None
    ambiguous = len(usages) > 1 or started > 1 or len(threads) > 1
    if ambiguous:
        errors.append("unsupported_multiple_turns_or_usage")
    elif len(usages) == 1:
        evidence_line, usage = usages[0]
        if isinstance(usage, dict):
            for field in FIELDS:
                value = usage.get(field)
                if type(value) is int and value >= 0:
                    tokens[field] = value
                elif value is not None:
                    errors.append(f"invalid_usage_field:{field}")
            for subset, total in (
                ("cached_input_tokens", "input_tokens"),
                ("reasoning_output_tokens", "output_tokens"),
            ):
                if tokens[subset] is not None and tokens[total] is not None:
                    if tokens[subset] > tokens[total]:
                        tokens[subset] = None
                        errors.append(f"invalid_usage_subset:{subset}")
        else:
            errors.append("usage_not_object")
    else:
        errors.append("final_usage_missing")
    required = all(tokens[name] is not None for name in ("input_tokens", "output_tokens"))
    status = "complete" if required and not damaged and not errors else "partial"
    if not any(tokens[name] is not None for name in ("input_tokens", "output_tokens")):
        status = "unknown"
    tokens["total_tokens"] = (
        tokens["input_tokens"] + tokens["output_tokens"] if required else None
    )
    return {
        "tokens": tokens,
        "usage_status": status,
        "collection_errors": errors,
        "session_id": threads[0] if len(threads) == 1 else None,
        "evidence_line": evidence_line,
        "events_bytes": cutoff,
        "turn_started_count": started,
        "usage_event_count": len(usages),
    }


def collect_run(directory: Path, metadata: dict | None = None) -> dict:
    metadata = metadata if metadata is not None else read_json(directory / "run.json")
    parsed = parse_events(directory / "events.jsonl")
    errors = parsed["collection_errors"]
    errors.extend(metadata.get("collection_errors", []))
    receipts = []
    models = set()
    for path in sorted((directory / "hooks").glob("*.json")):
        try:
            receipt = read_json(path)
            if (
                any(receipt.get(key) != metadata.get(key) for key in ("run_id", "job_id", "role"))
                or receipt.get("event") not in HOOK_EVENTS
                or not isinstance(receipt.get("session_id"), str)
                or (
                    parsed["session_id"] is not None
                    and receipt["session_id"] != parsed["session_id"]
                )
            ):
                raise ValueError("hook context mismatch")
            receipts.append(receipt)
            if isinstance(receipt.get("model"), str) and receipt["model"]:
                models.add(receipt["model"])
        except (OSError, ValueError):
            errors.append(f"invalid_hook_receipt:{path.name}")
    if metadata.get("hooks_expected") and not receipts:
        errors.append("hooks_unobserved:disabled_untrusted_or_missing")
    provisional = not (
        metadata.get("status") in {"finished", "failed", "interrupted"}
        and metadata.get("finished_at")
        and type(metadata.get("exit_code")) is int
        and metadata.get("output_closed", True)
    )
    if provisional and parsed["usage_status"] == "complete":
        parsed["usage_status"] = "partial"
    return {
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        **{
            key: metadata.get(key)
            for key in (
                "run_id", "job_id", "role", "check_only", "inspect_presentation", "status",
                "started_at", "finished_at", "exit_code", "codex_executable", "codex_version",
                "requested_model", "model_source", "requested_reasoning_effort", "reasoning_source",
            )
        },
        **parsed,
        "collection_errors": list(dict.fromkeys(errors)),
        "provisional": provisional,
        "observed_model": next(iter(models)) if len(models) == 1 else None,
        "observed_models": sorted(models),
        "hook_status": "observed" if receipts else "missing" if metadata.get("hooks_expected") else "unknown",
        "hook_events": [r["event"] for r in receipts],
        "hook_receipt_count": len(receipts),
        "generated_at": timestamp(),
        "report_status": "saved",
    }


def cell(value) -> str:
    if value is None:
        return "미확인"
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def local_time(value) -> str:
    if not value:
        return "미확인"
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            return f"{cell(value)} (시간대 미확인)"
        return moment.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    except (ValueError, TypeError):
        return cell(value)


def run_markdown(record: dict) -> str:
    lines = [
        f"# 역할 실행 사용량: {cell(record['run_id'])}", "",
        "| 항목 | 값 |", "|---|---|",
    ]
    for label, value in (
        ("학습 작업", record["job_id"]), ("역할", record["role"]),
        ("실행 종류", "진단" if record.get("check_only") else "학습"),
        ("실행 상태", record.get("status")), ("종료 코드", record.get("exit_code")),
        ("토큰 수집", record["usage_status"]), ("훅 수집", record["hook_status"]),
        ("관측 훅", ", ".join(record["hook_events"]) or None),
        ("잠정 보고서", record["provisional"]),
        ("CLI", record.get("codex_executable")), ("CLI 버전", record.get("codex_version")),
        ("요청 모델", record.get("requested_model")), ("모델 설정 출처", record.get("model_source")),
        ("Codex 보고 모델", ", ".join(record["observed_models"]) or None),
        ("요청 추론 강도", record.get("requested_reasoning_effort")),
        ("추론 설정 출처", record.get("reasoning_source")),
        ("시작", local_time(record.get("started_at"))),
        ("종료", local_time(record.get("finished_at"))),
        ("보고서 생성", local_time(record["generated_at"])),
        ("근거", f"events.jsonl:{record['evidence_line']} / {record['events_bytes']} bytes"),
        ("파서", record["parser_version"]),
    ):
        lines.append(f"| {label} | {cell(value)} |")
    lines.extend(["", "| 토큰 항목 | 수량 |", "|---|---:|"])
    for field, value in record["tokens"].items():
        lines.append(f"| {field} | {cell(value)} |")
    lines.extend([
        "", "전체 토큰은 입력 + 출력입니다. 캐시 입력·추론 출력은 세부 항목으로 중복 합산하지 않습니다.",
        "부분 수집량과 잠정 값은 작업의 완전 수집 합계에서 제외합니다.", "",
        "수집 오류: " + (", ".join(cell(e) for e in record["collection_errors"]) or "없음"), "",
    ])
    return "\n".join(lines)


def rebuild_run(root: Path, run_id: str, *, metadata: dict | None = None) -> dict:
    directory = run_directory(root, run_id)
    if not directory.is_dir():
        raise ValueError(f"실행을 찾을 수 없습니다: {run_id}")
    with report_lock(directory / "usage.lock"):
        record = collect_run(directory, metadata)
        if record.get("run_id") != run_id or record.get("role") not in ROLES:
            raise ValueError("실행 메타데이터가 일치하지 않습니다.")
        atomic_write(directory / "usage.md", run_markdown(record))
        atomic_json(directory / "usage.json", record)
    return record


def job_markdown(job_id: str, records: list[dict], errors: list[str]) -> str:
    lines = [f"# 학습 작업 사용량: {job_id}", "", f"집계 시각: {local_time(timestamp())}", "",
             "합계 범위: 종료가 확인된 완전 수집 실행. 부분·미확인·잠정 실행은 제외합니다.", ""]
    for diagnostic in (False, True):
        lines.extend([
            "## " + ("진단 실행" if diagnostic else "학습 실행"), "",
            "| 역할 | 실행 수 | 완전 | 부분 | 미확인 | 실패 | 중단 | 잠정 | 입력 | 캐시 입력 | 출력 | 추론 출력 | 전체 | 부분 입력 | 부분 출력 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for role in ROLES:
            runs = [r for r in records if r.get("role") == role and bool(r.get("check_only")) == diagnostic]
            complete = [r for r in runs if r["usage_status"] == "complete" and not r["provisional"]]
            partial = [r for r in runs if r["usage_status"] == "partial"]

            def total(selected, field):
                values = [r["tokens"].get(field) for r in selected]
                known = [v for v in values if v is not None]
                if not selected:
                    return 0
                result = str(sum(known)) if known else "미확인"
                if len(known) < len(values):
                    result += f" (제공 {len(known)}/{len(values)})"
                return result

            values = [role, len(runs), len(complete), len(partial),
                      sum(r["usage_status"] == "unknown" for r in runs),
                      sum(r.get("status") == "failed" for r in runs),
                      sum(r.get("status") == "interrupted" for r in runs),
                      sum(r["provisional"] for r in runs),
                      *[total(complete, f) for f in (*FIELDS, "total_tokens")],
                      total(partial, "input_tokens"), total(partial, "output_tokens")]
            lines.append("| " + " | ".join(cell(v) for v in values) + " |")
        lines.append("")
    lines.extend(["## 실행 이력", "", "| 실행 | 역할 | 종류 | 실행 상태 | 수집 | 훅 | 전체 토큰 |",
                  "|---|---|---|---|---|---|---:|"])
    for record in sorted(records, key=lambda r: (r.get("started_at") or "", r["run_id"])):
        run_id = safe_id(record["run_id"])
        reference = (
            f"[{run_id}](../../runs/{run_id}/usage.md)"
            if record.get("run_report_available", True) else run_id
        )
        lines.append("| " + " | ".join([
            reference, cell(record["role"]),
            "진단" if record.get("check_only") else "학습", cell(record.get("status")),
            cell(record["usage_status"]) + (" (잠정)" if record["provisional"] else ""),
            cell(record["hook_status"]), cell(record["tokens"]["total_tokens"]),
        ]) + " |")
    lines.extend(["", "집계 오류: " + (", ".join(cell(e) for e in errors) or "없음"), ""])
    return "\n".join(lines)


def rebuild_job(root: Path, job_id: str, *, refresh_runs: bool = True) -> Path:
    safe_id(job_id)
    parent = root / ".cs-study/runs"
    directories = sorted(parent.glob("*/run.json"))
    refresh_errors = []
    if refresh_runs:
        for path in directories:
            try:
                meta = read_json(path)
                if meta.get("job_id") == job_id:
                    rebuild_run(root, path.parent.name)
            except (OSError, ValueError) as exc:
                refresh_errors.append(f"run_rebuild_failed:{path.parent.name}:{type(exc).__name__}")
    target = root / ".cs-study/usage/jobs" / f"{job_id}.md"
    with report_lock(target.with_suffix(".lock")):
        records = []
        errors = refresh_errors.copy()
        found = False
        for path in sorted(parent.glob("*/run.json")):
            try:
                meta = read_json(path)
                if meta.get("job_id") != job_id:
                    continue
                found = True
                # Collect the source anew, never increment an existing total or trust stale usage.json.
                record = collect_run(run_directory(root, path.parent.name), meta)
                if record.get("run_id") != path.parent.name or record.get("role") not in ROLES:
                    raise ValueError("run context mismatch")
                record["run_report_available"] = (path.parent / "usage.md").exists()
                if not record["run_report_available"]:
                    errors.append(f"run_report_missing:{path.parent.name}")
                records.append(record)
            except (OSError, ValueError) as exc:
                errors.append(f"run_unreadable:{path.parent.name}:{type(exc).__name__}")
        if not found:
            raise ValueError(f"학습 작업의 실행 기록을 찾을 수 없습니다: {job_id}")
        atomic_write(target, job_markdown(job_id, records, errors))
    return target


def usage_report(root: Path, *, run_id: str | None = None, job_id: str | None = None,
                 rebuild: bool = False) -> str:
    if bool(run_id) == bool(job_id):
        raise ValueError("--run-id 또는 --job-id 중 하나를 지정하세요.")
    if run_id:
        directory = run_directory(root, run_id)
        if rebuild:
            record = rebuild_run(root, run_id)
            rebuild_job(root, record["job_id"], refresh_runs=False)
        path = directory / "usage.md"
    else:
        safe_id(job_id)
        path = root / ".cs-study/usage/jobs" / f"{job_id}.md"
        if rebuild:
            path = rebuild_job(root, job_id)
    if not path.is_file():
        raise ValueError("저장된 보고서가 없습니다. --rebuild로 원본 기록을 재처리하세요.")
    return path.read_text(encoding="utf-8")
