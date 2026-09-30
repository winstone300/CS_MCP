import json
import shutil
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, topic TEXT NOT NULL, level TEXT NOT NULL,
 question_count INTEGER NOT NULL, status TEXT NOT NULL,
 rules_text TEXT NOT NULL, rules_hash TEXT NOT NULL,
 research_revision INTEGER NOT NULL DEFAULT 0,
 parent_page_id TEXT, current_draft_version INTEGER,
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
 job_id TEXT NOT NULL REFERENCES jobs(id), source_id TEXT NOT NULL, content TEXT NOT NULL,
 PRIMARY KEY(job_id, source_id)
);
CREATE TABLE IF NOT EXISTS research_requests (
 job_id TEXT NOT NULL REFERENCES jobs(id), request_id TEXT NOT NULL, reason TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(job_id, request_id)
);
CREATE TABLE IF NOT EXISTS sections (
 job_id TEXT NOT NULL REFERENCES jobs(id), kind TEXT NOT NULL, version INTEGER NOT NULL,
 content TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(job_id, kind, version)
);
CREATE TABLE IF NOT EXISTS cross_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
 reviewer TEXT NOT NULL, foundation_version INTEGER NOT NULL, advanced_version INTEGER NOT NULL,
 research_revision INTEGER NOT NULL, content TEXT NOT NULL, passed INTEGER NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
 job_id TEXT NOT NULL REFERENCES jobs(id), version INTEGER NOT NULL,
 content TEXT NOT NULL, markdown TEXT NOT NULL, content_hash TEXT NOT NULL,
 foundation_version INTEGER NOT NULL, advanced_version INTEGER NOT NULL,
 research_revision INTEGER NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(job_id, version)
);
CREATE TABLE IF NOT EXISTS reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
 draft_version INTEGER NOT NULL, draft_hash TEXT NOT NULL, parent_page_id TEXT,
 decision TEXT NOT NULL, user_message TEXT NOT NULL, feedback TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publications (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), attempt_id TEXT NOT NULL UNIQUE,
 draft_version INTEGER NOT NULL, draft_hash TEXT NOT NULL, parent_page_id TEXT NOT NULL,
 status TEXT NOT NULL, page_id TEXT, page_url TEXT,
 error TEXT, observed_hash TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
 kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
PRAGMA user_version = 1;
"""

MIGRATION_V2 = """
ALTER TABLE jobs ADD COLUMN presentation_profile TEXT;
ALTER TABLE drafts ADD COLUMN bundle_hash TEXT;
ALTER TABLE drafts ADD COLUMN manifest TEXT;
ALTER TABLE drafts ADD COLUMN notion_markdown TEXT;
ALTER TABLE drafts ADD COLUMN preview_html TEXT;
ALTER TABLE reviews ADD COLUMN bundle_hash TEXT;
ALTER TABLE publications ADD COLUMN bundle_hash TEXT;
CREATE TABLE assets (
 job_id TEXT NOT NULL REFERENCES jobs(id), hash TEXT NOT NULL,
 metadata TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(job_id,hash)
);
CREATE TABLE section_assets (
 job_id TEXT NOT NULL, kind TEXT NOT NULL, version INTEGER NOT NULL,
 visual_id TEXT NOT NULL, metadata TEXT NOT NULL,
 PRIMARY KEY(job_id,kind,version,visual_id),
 FOREIGN KEY(job_id,kind,version) REFERENCES sections(job_id,kind,version)
);
CREATE TABLE publication_assets (
 job_id TEXT NOT NULL REFERENCES jobs(id), asset_hash TEXT NOT NULL,
 receipt TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(job_id,asset_hash)
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Database:
    def __init__(self, root: Path):
        self.directory = root / ".cs-study"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "study.sqlite3"
        db = sqlite3.connect(self.path, timeout=15)
        try:
            # Switching a brand-new file to WAL can return SQLITE_BUSY immediately
            # instead of observing busy_timeout when several role servers start.
            deadline = time.monotonic() + 15
            while True:
                try:
                    if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                        db.execute("PRAGMA journal_mode = WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if (getattr(exc, "sqlite_errorcode", 0) & 255) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError(f"지원하지 않는 DB 스키마 버전: {version}")
            if version < 2:
                # Backup API includes committed WAL frames. Never copy an open .sqlite file.
                if version == 1:
                    backup = (
                        self.directory
                        / "backups"
                        / ("schema-v1-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f"))
                    )
                    backup.mkdir(parents=True)
                    with sqlite3.connect(backup / "study.sqlite3") as target:
                        db.backup(target)
                    assets = self.directory / "assets"
                    if assets.is_dir():
                        shutil.copytree(assets, backup / "assets")
                try:
                    db.execute("BEGIN IMMEDIATE")
                    # Both fresh schema creation and migration share the same lock.
                    current = db.execute("PRAGMA user_version").fetchone()[0]
                    if current == 0:
                        for statement in SCHEMA.split(";"):
                            if statement.strip():
                                db.execute(statement)
                    if current < 2:
                        for statement in MIGRATION_V2.split(";"):
                            if statement.strip():
                                db.execute(statement)
                        from .presentation import profile_snapshot

                        db.execute(
                            "UPDATE jobs SET presentation_profile=? WHERE presentation_profile IS NULL",
                            (dumps(profile_snapshot("legacy_v1")),),
                        )
                        db.execute("PRAGMA user_version = 2")
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
        finally:
            db.close()

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def event(db: sqlite3.Connection, job_id: str, kind: str, payload: object) -> None:
    db.execute(
        "INSERT INTO events(job_id, kind, payload, created_at) VALUES (?,?,?,?)",
        (job_id, kind, dumps(payload), now()),
    )
