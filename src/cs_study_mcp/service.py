"""Transactional workflow gates, immutable artifacts, and conservative publication recovery."""

import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from .models import (
    AdvancedSection,
    CrossReview,
    DraftInput,
    FoundationSection,
    KnowledgeSection,
    PreviewReference,
    PublicationInput,
    ReviewInput,
    SectionKind,
    Source,
)
from .presentation import profile_snapshot
from .presentation_service import PresentationService, is_readable, is_topic, profile_for
from .render import digest, normalize_markdown, render_document
from .settings import configured_parent, notion_id
from .storage import Database, dumps, event, now
from .validation import combined_question_issues, question_blueprint, section_issues

SECTION_ADAPTER = TypeAdapter(KnowledgeSection)


class WorkflowError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class StudyService(PresentationService):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.db = Database(self.root)

    @staticmethod
    def _job(db: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise WorkflowError("not_found", "작업을 찾을 수 없습니다.")
        return row

    def _editable(self, db: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        job = self._job(db, job_id)
        if db.execute("SELECT 1 FROM publications WHERE job_id=?", (job_id,)).fetchone():
            raise WorkflowError(
                "publication_locked", "발행을 시작한 문서는 수정할 수 없습니다. 새 작업을 만드세요."
            )
        return job

    @staticmethod
    def _invalidate(db: sqlite3.Connection, job_id: str, status: str = "drafting") -> None:
        db.execute(
            "UPDATE jobs SET status=?,current_draft_version=NULL,error=NULL,updated_at=? WHERE id=?",
            (status, now(), job_id),
        )

    @staticmethod
    def _source_map(db: sqlite3.Connection, job_id: str) -> dict[str, Source]:
        return {
            row["source_id"]: Source.model_validate_json(row["content"])
            for row in db.execute(
                "SELECT * FROM sources WHERE job_id=? ORDER BY source_id", (job_id,)
            )
        }

    @staticmethod
    def _section(
        db: sqlite3.Connection, job_id: str, kind: SectionKind, version: int | None = None
    ) -> sqlite3.Row | None:
        if version is None:
            return db.execute(
                "SELECT * FROM sections WHERE job_id=? AND kind=? ORDER BY version DESC LIMIT 1",
                (job_id, kind),
            ).fetchone()
        return db.execute(
            "SELECT * FROM sections WHERE job_id=? AND kind=? AND version=?",
            (job_id, kind, version),
        ).fetchone()

    @staticmethod
    def _draft(db: sqlite3.Connection, job: sqlite3.Row) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM drafts WHERE job_id=? AND version=?",
            (job["id"], job["current_draft_version"]),
        ).fetchone()
        if row is None:
            raise WorkflowError(
                "no_current_draft", "현재 부분 문서를 검토하고 통합 초안을 다시 저장하세요."
            )
        return row

    def create_study(
        self,
        topic: str,
        level: str = "CS 기본 지식·면접 준비",
        question_count: int = 6,
        presentation_profile: str = "study_topic_v3",
        visual_transport: str = "mermaid",
    ) -> dict:
        topic, level = topic.strip(), level.strip()
        if not topic or not level:
            raise WorkflowError("invalid_input", "주제와 학습 수준을 입력하세요.")
        question_blueprint("foundation", question_count)
        rules_path = self.root / "AGENTS.md"
        if not rules_path.is_file():
            raise WorkflowError("missing_rules", "프로젝트 루트에 AGENTS.md가 필요합니다.")
        rules = rules_path.read_text(encoding="utf-8-sig")
        if not rules.strip():
            raise WorkflowError("missing_rules", "AGENTS.md가 비어 있습니다.")
        job_id = str(uuid4())
        parent = configured_parent(self.root)
        profile = profile_snapshot(presentation_profile)
        if visual_transport not in ("mermaid", "image"):
            raise WorkflowError("invalid_transport", "mermaid 또는 image를 선택하세요.")
        if presentation_profile != "legacy_v1":
            profile["diagram_transport"] = visual_transport
        with self.db.connect(write=True) as db:
            db.execute(
                "INSERT INTO jobs(id,topic,level,question_count,status,rules_text,rules_hash,"
                "parent_page_id,created_at,updated_at,presentation_profile) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    topic,
                    level,
                    question_count,
                    "researching",
                    rules,
                    digest(rules),
                    parent,
                    now(),
                    now(),
                    dumps(profile),
                ),
            )
            event(db, job_id, "created", {"rules_hash": digest(rules)})
        return {
            "job_id": job_id,
            "rules_hash": digest(rules),
            "rules_text": rules,
            "parent_page_id": parent,
            "presentation_profile": profile,
            "presentation_profile_hash": digest(dumps(profile)),
        }

    def list_studies(self, limit: int = 20) -> list[dict]:
        if not 1 <= limit <= 100:
            raise WorkflowError("invalid_input", "limit은 1~100이어야 합니다.")
        with self.db.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,topic,status,current_draft_version,error,updated_at FROM jobs ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                )
            ]

    def request_research_followup(self, job_id: str, request_id: str, reason: str) -> dict:
        if not request_id.strip() or not reason.strip():
            raise WorkflowError("invalid_input", "재검색 요청 ID와 사유가 필요합니다.")
        with self.db.connect(write=True) as db:
            self._editable(db, job_id)
            old = db.execute(
                "SELECT reason FROM research_requests WHERE job_id=? AND request_id=?",
                (job_id, request_id),
            ).fetchone()
            count = db.execute(
                "SELECT COUNT(*) FROM research_requests WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            if old:
                if old["reason"] != reason:
                    raise WorkflowError("request_conflict", "같은 요청 ID의 사유가 변경되었습니다.")
                return {"allowed": True, "round": count, "idempotent": True}
            if count >= 2:
                db.execute(
                    "UPDATE jobs SET status='needs_attention',error=?,updated_at=? WHERE id=?",
                    ("추가 검색 한도(작업 전체 2회)에 도달했습니다.", now(), job_id),
                )
                return {"allowed": False, "round": count, "status": "needs_attention"}
            db.execute(
                "INSERT INTO research_requests VALUES(?,?,?,?)", (job_id, request_id, reason, now())
            )
            self._invalidate(db, job_id, "researching")
            event(db, job_id, "research_followup", {"request_id": request_id, "reason": reason})
            return {"allowed": True, "round": count + 1, "idempotent": False}

    def save_research(self, job_id: str, sources: list[Source]) -> dict:
        if not sources:
            raise WorkflowError("invalid_input", "출처가 하나 이상 필요합니다.")
        with self.db.connect(write=True) as db:
            self._editable(db, job_id)
            inserted = []
            for source in sources:
                content = dumps(source.model_dump(mode="json"))
                previous = db.execute(
                    "SELECT content FROM sources WHERE job_id=? AND source_id=?",
                    (job_id, source.id),
                ).fetchone()
                if previous:
                    if previous["content"] != content:
                        raise WorkflowError(
                            "source_immutable",
                            "출처 ID는 불변입니다. 변경된 원문은 새 ID로 저장하세요.",
                        )
                    continue
                db.execute("INSERT INTO sources VALUES(?,?,?)", (job_id, source.id, content))
                inserted.append(source.id)
            if inserted:
                db.execute(
                    "UPDATE jobs SET research_revision=research_revision+1 WHERE id=?", (job_id,)
                )
                self._invalidate(db, job_id)
                event(db, job_id, "research_saved", {"source_ids": inserted})
            return {"source_ids": [source.id for source in sources], "inserted": inserted}

    def save_knowledge_section(
        self, job_id: str, content: KnowledgeSection, expected_version: int
    ) -> dict:
        with self.db.connect(write=True) as db:
            job = self._editable(db, job_id)
            if not is_readable(job) and (content.visuals or content.comparisons):
                raise WorkflowError("legacy_profile", "시각 자료는 새 학습 작업에 저장하세요.")
            previous = self._section(db, job_id, content.kind)
            version = previous["version"] if previous else 0
            if version != expected_version:
                raise WorkflowError(
                    "version_conflict", f"현재 {content.kind} 버전은 {version}입니다."
                )
            if isinstance(content, AdvancedSection):
                if self._section(db, job_id, "foundation", content.foundation_version) is None:
                    raise WorkflowError("missing_foundation", "참조한 기초 문서가 없습니다.")
            serialized = dumps(content.model_dump(mode="json"))
            # Normalize both sides with the same model: added defaults must not invalidate
            # a historical section, its cross reviews, or the approved draft.
            if previous and SECTION_ADAPTER.validate_json(previous["content"]).model_dump(
                mode="json"
            ) == content.model_dump(mode="json"):
                return {
                    "kind": content.kind,
                    "version": version,
                    "hash": previous["content_hash"],
                    "unchanged": True,
                }
            version += 1
            db.execute(
                "INSERT INTO sections VALUES(?,?,?,?,?,?)",
                (job_id, content.kind, version, serialized, digest(serialized), now()),
            )
            self._invalidate(db, job_id)
            event(db, job_id, "section_saved", {"kind": content.kind, "version": version})
            return {
                "kind": content.kind,
                "version": version,
                "hash": digest(serialized),
                "unchanged": False,
            }

    def get_knowledge_section(
        self, job_id: str, kind: SectionKind, version: int | None = None
    ) -> dict:
        with self.db.connect() as db:
            self._job(db, job_id)
            row = self._section(db, job_id, kind, version)
            if row is None:
                raise WorkflowError("section_not_found", "해당 부분 문서가 없습니다.")
            return {
                "kind": kind,
                "version": row["version"],
                "hash": row["content_hash"],
                "content": json.loads(row["content"]),
            }

    def _validation(self, db: sqlite3.Connection, job: sqlite3.Row) -> dict:
        sources = self._source_map(db, job["id"])
        rows = {kind: self._section(db, job["id"], kind) for kind in ("foundation", "advanced")}
        issues: list[dict] = []
        sections = {}
        for kind, row in rows.items():
            if row is None:
                issues.append(
                    {"code": "missing_section", "path": kind, "message": "부분 문서가 없습니다."}
                )
                continue
            section = SECTION_ADAPTER.validate_json(row["content"])
            sections[kind] = section
            if not is_readable(job) and isinstance(section, FoundationSection):
                for index, term in enumerate(section.terms):
                    if not term.why or not term.example:
                        issues.append(
                            {
                                "code": "missing_term_detail",
                                "path": f"foundation.terms.{index}",
                                "message": "기존 형식의 용어는 필요성과 예시가 필수입니다.",
                            }
                        )
            issues.extend(
                section_issues(
                    section,
                    sources,
                    job["question_count"],
                    rows["foundation"]["version"] if rows["foundation"] else 0,
                )
            )
        if len(sections) == 2:
            issues.extend(combined_question_issues(sections["foundation"], sections["advanced"]))
        return {
            "valid": not issues,
            "issues": issues,
            "versions": {kind: row["version"] if row else None for kind, row in rows.items()},
            "research_revision": job["research_revision"],
            "scope": "구조·인용 연결 검사입니다. 사실 판단은 Codex 및 사람의 검토가 필요합니다.",
        }

    def validate_knowledge(self, job_id: str) -> dict:
        with self.db.connect() as db:
            return self._validation(db, self._job(db, job_id))

    def record_cross_review(self, job_id: str, review: CrossReview) -> dict:
        with self.db.connect(write=True) as db:
            job = self._editable(db, job_id)
            if review.research_revision != job["research_revision"]:
                raise WorkflowError(
                    "stale_research",
                    "검토 중 조사 자료가 변경되었습니다. 최신 근거를 다시 확인하세요.",
                )
            for kind in ("foundation", "advanced"):
                row = self._section(db, job_id, kind)
                if row is None or row["version"] != getattr(review, f"{kind}_version"):
                    raise WorkflowError(
                        "stale_review", "교차 검토는 최신 기초·심화 버전을 대상으로 수행하세요."
                    )
            passed = not any(item.severity == "blocking" for item in review.findings)
            if is_readable(job):
                presentation = self._presentation(db, job)
                if review.presentation_hash != presentation["presentation_hash"]:
                    raise WorkflowError(
                        "stale_visual_review",
                        "실제 읽은 시각 자료 묶음의 presentation_hash가 필요합니다.",
                    )
                if passed and not presentation["valid"]:
                    raise WorkflowError("presentation_invalid", dumps(presentation["errors"]))
            db.execute(
                "INSERT INTO cross_reviews(job_id,reviewer,foundation_version,advanced_version,"
                "research_revision,content,passed,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    review.reviewer,
                    review.foundation_version,
                    review.advanced_version,
                    job["research_revision"],
                    review.model_dump_json(),
                    int(passed),
                    now(),
                ),
            )
            self._invalidate(db, job_id, "reviewing" if passed else "needs_attention")
            event(db, job_id, "cross_review", {"reviewer": review.reviewer, "passed": passed})
            return {"passed": passed, "reviewer": review.reviewer}

    def _require_cross_reviews(
        self, db: sqlite3.Connection, job: sqlite3.Row, f: int, a: int
    ) -> None:
        for role in ("foundation", "advanced"):
            review = db.execute(
                "SELECT passed,content FROM cross_reviews WHERE job_id=? AND reviewer=? AND foundation_version=? "
                "AND advanced_version=? AND research_revision=? ORDER BY id DESC LIMIT 1",
                (job["id"], role, f, a, job["research_revision"]),
            ).fetchone()
            if not review or not review["passed"]:
                raise WorkflowError(
                    "cross_review_required", f"{role}의 최신 교차 검토 통과가 필요합니다."
                )
            if (
                is_readable(job)
                and json.loads(review["content"]).get("presentation_hash")
                != self._require_presentation(db, job)["presentation_hash"]
            ):
                raise WorkflowError(
                    "stale_visual_review", "실제 그림과 최신 배치를 다시 교차 검토하세요."
                )

    def save_draft(self, job_id: str, content: DraftInput | PreviewReference) -> dict:
        with self.db.connect(write=True) as db:
            job = self._editable(db, job_id)
            candidate = None
            if is_topic(job):
                if not isinstance(content, PreviewReference):
                    raise WorkflowError("preview_required", "v3 초안은 검토한 preview_version과 presentation_hash로 저장하세요.")
                candidate = self._preview_row(db, job_id)
                presentation = self._require_presentation(db, job)
                if (candidate["version"] != content.preview_version
                        or presentation["presentation_hash"] != content.presentation_hash):
                    raise WorkflowError("stale_preview", "검토한 최신 미리보기 후보가 필요합니다.")
                plan = json.loads(candidate["plan"])
                content = DraftInput.model_validate({key: plan[key] for key in DraftInput.model_fields})
            elif not isinstance(content, DraftInput):
                raise WorkflowError("profile_required", "기존 작업에는 기존 DraftInput을 사용하세요.")
            report = self._validation(db, job)
            if not report["valid"]:
                raise WorkflowError("validation_failed", dumps(report["issues"]))
            for kind in ("foundation", "advanced"):
                if report["versions"][kind] != getattr(content, f"{kind}_version"):
                    raise WorkflowError("stale_draft", "최신 부분 문서 버전을 사용하세요.")
            self._require_cross_reviews(
                db, job, content.foundation_version, content.advanced_version
            )
            foundation = FoundationSection.model_validate_json(
                self._section(db, job_id, "foundation")["content"]
            )
            advanced = AdvancedSection.model_validate_json(
                self._section(db, job_id, "advanced")["content"]
            )
            for point in content.summary:
                section = foundation if point.section == "foundation" else advanced
                if not set(point.claim_ids) <= {claim.id for claim in section.claims}:
                    raise WorkflowError("unknown_claim", "요약의 주장 ID를 확인하세요.")
            manifest = bundle_hash = notion_markdown = preview_html = None
            presentation_warnings = []
            if is_readable(job):
                from .render_v2 import render_readable

                presentation = self._require_presentation(db, job)
                presentation_warnings = list(presentation["warnings"])
                recommendations = profile_for(job)["recommendations"]
                if (
                    not recommendations["summary_min"]
                    <= len(content.summary)
                    <= recommendations["summary_max"]
                ):
                    presentation_warnings.append(
                        {
                            "code": "summary_length",
                            "path": "summary",
                            "message": "핵심 요약은 3~5개를 권장합니다.",
                        }
                    )
                preview_assets = [
                    {**asset, "path": str((self.root / asset["path"]).resolve())}
                    for asset in presentation["assets"]
                ]
                rendered = json.loads(candidate["rendered"]) if candidate is not None else render_readable(
                    content, foundation, advanced, self._source_map(db, job_id),
                    profile_for(job), preview_assets,
                )
                markdown, notion_markdown, preview_html = (
                    rendered["markdown"],
                    rendered["notion_markdown"],
                    rendered["html"],
                )
                manifest = {
                    "hash_kind": "presentation_bundle_v2",
                    "profile": profile_for(job),
                    "presentation_hash": presentation["presentation_hash"],
                    "markdown_hash": digest(markdown),
                    "notion_hash": digest(notion_markdown),
                    "preview_hash": digest(preview_html),
                    "blocks": rendered["blocks"],
                    "assets": presentation["assets"],
                    "research_revision": job["research_revision"],
                }
                if candidate is not None:
                    manifest.update(hash_kind="presentation_bundle_v3",
                                    composition_version=candidate["version"],
                                    plan_hash=digest(candidate["plan"]))
                bundle_hash = digest(dumps(manifest))
            else:
                markdown, _ = render_document(
                    content, foundation, advanced, self._source_map(db, job_id)
                )
            version = db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM drafts WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO drafts(job_id,version,content,markdown,content_hash,foundation_version,advanced_version,research_revision,created_at,bundle_hash,manifest,notion_markdown,preview_html) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    version,
                    content.model_dump_json(),
                    markdown,
                    digest(markdown),
                    content.foundation_version,
                    content.advanced_version,
                    job["research_revision"],
                    now(),
                    bundle_hash,
                    dumps(manifest) if manifest else None,
                    notion_markdown,
                    preview_html,
                ),
            )
            db.execute(
                "UPDATE jobs SET current_draft_version=?,status='awaiting_review',error=NULL,updated_at=? WHERE id=?",
                (version, now(), job_id),
            )
            event(db, job_id, "draft_saved", {"version": version, "hash": digest(markdown)})
        result = {
            "version": version,
            "hash": digest(markdown),
            "markdown": markdown,
            "status": "awaiting_review",
            "bundle_hash": bundle_hash,
            "hash_kind": manifest["hash_kind"] if bundle_hash else "markdown_v1",
            "presentation_warnings": presentation_warnings,
        }
        try:
            result["path"] = str(self._export(job_id, version, markdown))
            result["preview_path"] = result["path"]
        except OSError as exc:
            result["export_error"] = str(exc)
            result["instructions"] = (
                "초안은 저장되었습니다. export를 재시도하고 save_draft를 반복하지 마세요."
            )
        return result

    def _export(self, job_id: str, version: int, markdown: str) -> Path:
        directory = self.db.directory / "drafts" / job_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"v{version}.md"
        fd, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
                file.write(markdown)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return destination

    def export_draft(self, job_id: str) -> Path:
        with self.db.connect() as db:
            draft = self._draft(db, self._job(db, job_id))
            version, markdown = draft["version"], draft["markdown"]
        return self._export(job_id, version, markdown)

    def record_review(self, job_id: str, review: ReviewInput) -> dict:
        with self.db.connect(write=True) as db:
            job = self._editable(db, job_id)
            draft = self._draft(db, job)
            if review.decision == "approve":
                self._check_bundle(db, job, draft)
            if (
                draft["version"] != review.draft_version
                or draft["content_hash"] != review.draft_hash
            ):
                raise WorkflowError(
                    "stale_approval", "표시된 최신 초안의 버전과 해시를 확인하세요."
                )
            if is_readable(job) and review.bundle_hash != draft["bundle_hash"]:
                raise WorkflowError("stale_approval", "표시된 초안의 bundle_hash를 확인하세요.")
            parent = notion_id(review.parent_page_id)
            if review.decision == "approve":
                current_parent = configured_parent(self.root)
                if current_parent is None:
                    raise WorkflowError(
                        "target_not_configured",
                        "먼저 configure --notion-parent로 발행 대상을 지정하세요.",
                    )
                if parent != current_parent:
                    raise WorkflowError(
                        "target_changed",
                        "사용자에게 보여준 대상과 현재 설정이 다릅니다. 대상을 다시 확인받으세요.",
                    )
            db.execute(
                "INSERT INTO reviews(job_id,draft_version,draft_hash,parent_page_id,decision,user_message,"
                "feedback,created_at,bundle_hash) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    review.draft_version,
                    review.draft_hash,
                    parent,
                    review.decision,
                    review.user_message,
                    review.feedback,
                    now(),
                    review.bundle_hash,
                ),
            )
            status = "approved" if review.decision == "approve" else "changes_requested"
            db.execute(
                "UPDATE jobs SET status=?,parent_page_id=?,error=NULL,updated_at=? WHERE id=?",
                (status, parent, now(), job_id),
            )
            event(
                db,
                job_id,
                "user_review_recorded",
                {"decision": review.decision, "draft_version": review.draft_version},
            )
            return {
                "status": status,
                "parent_page_id": parent,
                "draft_hash": review.draft_hash,
                "bundle_hash": review.bundle_hash,
                "approval_boundary": "Codex가 실제 사용자 메시지를 확인한 뒤 기록해야 합니다. 이 도구는 사람을 인증하지 않습니다.",
            }

    def prepare_publication(self, job_id: str, draft_version: int) -> dict:
        with self.db.connect(write=True) as db:
            job = self._job(db, job_id)
            draft = self._draft(db, job)
            self._check_bundle(db, job, draft)
            if draft["version"] != draft_version:
                raise WorkflowError("stale_draft", "승인된 최신 초안 버전만 발행할 수 있습니다.")
            approval = db.execute(
                "SELECT * FROM reviews WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)
            ).fetchone()
            if (
                not approval
                or approval["decision"] != "approve"
                or approval["draft_version"] != draft_version
                or approval["draft_hash"] != draft["content_hash"]
                or (is_readable(job) and approval["bundle_hash"] != draft["bundle_hash"])
            ):
                raise WorkflowError(
                    "approval_required", "최신 초안에 대한 사용자의 명시적 승인이 필요합니다."
                )
            previous = db.execute("SELECT * FROM publications WHERE job_id=?", (job_id,)).fetchone()
            parent = previous["parent_page_id"] if previous else configured_parent(self.root)
            if parent is None or parent != approval["parent_page_id"]:
                raise WorkflowError(
                    "target_changed",
                    "발행 대상이 변경되었습니다. 새 대상에 대해 다시 승인받으세요.",
                )
            if not self._validation(db, job)["valid"]:
                raise WorkflowError("validation_failed", "내용 검증을 다시 수행하세요.")
            self._require_cross_reviews(
                db, job, draft["foundation_version"], draft["advanced_version"]
            )
            if previous:
                action = (
                    "already_completed"
                    if previous["status"] == "completed"
                    else ("resume_existing_page" if previous["page_id"] else "inspect_before_retry")
                )
                if action == "inspect_before_retry":
                    db.execute(
                        "UPDATE publications SET status='needs_attention',error=?,updated_at=? WHERE job_id=?",
                        (
                            "이전 생성 결과를 확인하기 전에는 새 페이지를 만들 수 없습니다.",
                            now(),
                            job_id,
                        ),
                    )
                    db.execute(
                        "UPDATE jobs SET status='needs_attention',error=?,updated_at=? WHERE id=?",
                        ("페이지 생성 결과 확인이 필요합니다.", now(), job_id),
                    )
                attempt_id, page_id, page_url = (
                    previous["attempt_id"],
                    previous["page_id"],
                    previous["page_url"],
                )
            else:
                if job["status"] != "approved":
                    raise WorkflowError("approval_required", "승인 상태를 확인하세요.")
                attempt_id, page_id, page_url, action = str(uuid4()), None, None, "create_page"
                db.execute(
                    "INSERT INTO publications(job_id,attempt_id,draft_version,draft_hash,parent_page_id,status,created_at,updated_at,bundle_hash)"
                    " VALUES(?,?,?,?,?,'publishing',?,?,?)",
                    (
                        job_id,
                        attempt_id,
                        draft_version,
                        draft["content_hash"],
                        parent,
                        now(),
                        now(),
                        draft["bundle_hash"],
                    ),
                )
                db.execute(
                    "UPDATE jobs SET status='publishing',error=NULL,updated_at=? WHERE id=?",
                    (now(), job_id),
                )
                event(db, job_id, "publication_started", {"attempt_id": attempt_id})
            title = json.loads(draft["content"])["title"]
            body = (
                draft["notion_markdown"]
                if is_readable(job)
                else draft["markdown"].split("\n\n", 1)[1]
            )
            receipts = self._receipts(db, job_id)
            for receipt in receipts:
                body = body.replace("asset://" + receipt["hash"], receipt["remote_url"])
            return {
                "action": action,
                "attempt_id": attempt_id,
                "parent_page_id": parent,
                "page_id": page_id,
                "page_url": page_url,
                "title": title,
                "markdown": body,
                "draft_hash": draft["content_hash"],
                "body_hash": digest(normalize_markdown(body)),
                "bundle_hash": draft["bundle_hash"],
                "manifest": json.loads(draft["manifest"]) if draft["manifest"] else None,
                "asset_receipts": receipts,
                "required_uploads": sorted(set(re.findall(r"asset://([a-f0-9]{64})", body))),
                "instructions": "create_page는 한 번만 실행하세요. 기존 페이지는 조회 후 복구하고, 결과를 즉시 기록하세요.",
            }

    def record_publication(self, job_id: str, result: PublicationInput) -> dict:
        with self.db.connect(write=True) as db:
            job = self._job(db, job_id)
            publication = db.execute(
                "SELECT * FROM publications WHERE job_id=?", (job_id,)
            ).fetchone()
            if not publication or publication["attempt_id"] != result.attempt_id:
                raise WorkflowError("unknown_attempt", "발행 시도를 먼저 준비하세요.")
            page_id = notion_id(result.page_id) if result.page_id else publication["page_id"]
            if publication["page_id"] and page_id != publication["page_id"]:
                raise WorkflowError("page_conflict", "이미 연결된 페이지 ID를 변경할 수 없습니다.")
            page_url = str(result.page_url) if result.page_url else publication["page_url"]
            if page_url:
                try:
                    url_page_id = notion_id(page_url)
                except ValueError as exc:
                    raise WorkflowError(
                        "page_conflict", "유효한 Notion 페이지 URL이 필요합니다."
                    ) from exc
                if not page_id or url_page_id != page_id:
                    raise WorkflowError(
                        "page_conflict", "페이지 URL과 페이지 ID가 일치하지 않습니다."
                    )
            if page_id and not page_url:
                page_url = f"https://www.notion.so/{page_id.replace('-', '')}"
            if publication["status"] == "completed":
                return {
                    "status": "completed",
                    "page_id": page_id,
                    "page_url": page_url,
                    "idempotent": True,
                }
            if result.outcome == "page_created" and publication["page_id"] == page_id and page_id:
                return {
                    "status": publication["status"],
                    "page_id": page_id,
                    "page_url": publication["page_url"],
                    "error": publication["error"],
                    "idempotent": True,
                }
            error = result.error
            observed_hash = None
            if result.outcome == "page_created":
                if not page_id:
                    raise WorkflowError("page_required", "생성된 페이지 ID를 즉시 기록하세요.")
                status = "publishing"
            elif result.outcome == "verified":
                if (
                    not page_id
                    or result.observed_markdown is None
                    or result.observed_title is None
                    or result.observed_parent_page_id is None
                ):
                    raise WorkflowError(
                        "readback_required",
                        "페이지 ID와 실제 재조회한 상위 페이지·제목·본문이 필요합니다.",
                    )
                draft = db.execute(
                    "SELECT * FROM drafts WHERE job_id=? AND version=?",
                    (job_id, publication["draft_version"]),
                ).fetchone()
                expected = draft["markdown"].split("\n\n", 1)[1]
                observed_hash = digest(normalize_markdown(result.observed_markdown))
                matches = (
                    result.observed_title.strip() == json.loads(draft["content"])["title"]
                    and observed_hash == digest(normalize_markdown(expected))
                    and notion_id(result.observed_parent_page_id) == publication["parent_page_id"]
                )
                if is_readable(job):
                    from .publication import verify_readback

                    self._check_bundle(db, job, draft)
                    manifest = json.loads(draft["manifest"])
                    receipts = {r["hash"]: r for r in self._receipts(db, job_id)}
                    expected_assets = [
                        {**asset, **receipts.get(asset["hash"], {})} for asset in manifest["assets"]
                    ]
                    readback = verify_readback(
                        manifest["blocks"],
                        result.observed_markdown,
                        observed_blocks=result.observed_blocks,
                        expected_assets=expected_assets,
                        observed_assets=result.observed_assets,
                    )
                    observed_hash = readback["observed_hash"]
                    matches = (
                        readback["valid"]
                        and result.observed_title.strip() == json.loads(draft["content"])["title"]
                        and notion_id(result.observed_parent_page_id)
                        == publication["parent_page_id"]
                    )
                status = "completed" if matches else "needs_attention"
                error = (
                    None if matches else "재조회한 노션 대상·제목·본문이 승인된 초안과 다릅니다."
                )
            else:
                status = "needs_attention"
                error = error or result.outcome
            db.execute(
                "UPDATE publications SET status=?,page_id=?,page_url=?,error=?,observed_hash=?,updated_at=? WHERE job_id=?",
                (status, page_id, page_url, error, observed_hash, now(), job_id),
            )
            db.execute(
                "UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?",
                (status, error, now(), job_id),
            )
            event(
                db,
                job_id,
                "publication_recorded",
                {"outcome": result.outcome, "status": status, "page_id": page_id},
            )
            return {"status": status, "page_id": page_id, "page_url": page_url, "error": error}

    def get_study(self, job_id: str) -> dict:
        with self.db.connect() as db:
            job = dict(self._job(db, job_id))
            job["sources"] = [
                source.model_dump(mode="json") for source in self._source_map(db, job_id).values()
            ]
            job["sections"] = {}
            for kind in ("foundation", "advanced"):
                row = self._section(db, job_id, kind)
                job["sections"][kind] = (
                    None
                    if row is None
                    else {
                        "version": row["version"],
                        "hash": row["content_hash"],
                        "content": json.loads(row["content"]),
                    }
                )
            draft = db.execute(
                "SELECT * FROM drafts WHERE job_id=? AND version=?",
                (job_id, job["current_draft_version"]),
            ).fetchone()
            job["draft"] = dict(draft) if draft else None
            for table in ("cross_reviews", "reviews", "events"):
                job[table] = [
                    dict(row)
                    for row in db.execute(
                        f"SELECT * FROM {table} WHERE job_id=? ORDER BY id", (job_id,)
                    )
                ]
            publication = db.execute(
                "SELECT * FROM publications WHERE job_id=?", (job_id,)
            ).fetchone()
            job["publication"] = dict(publication) if publication else None
            job["research_followups"] = db.execute(
                "SELECT COUNT(*) FROM research_requests WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            job["configured_parent_page_id"] = configured_parent(self.root)
            job["presentation_profile"] = profile_for(job)
            job["presentation_profile_hash"] = digest(dumps(job["presentation_profile"]))
            job["asset_receipts"] = self._receipts(db, job_id)
            if job["presentation_profile"]["name"] == "study_topic_v3":
                preview = self._preview_row(db, job_id)
                job["document_preview"] = None if preview is None else {
                    "version": preview["version"], "presentation_hash": preview["presentation_hash"],
                    "instructions": "get_document_preview로 해당 버전의 실제 내용과 최신 의존성 일치 여부를 확인하세요.",
                }
            return job
