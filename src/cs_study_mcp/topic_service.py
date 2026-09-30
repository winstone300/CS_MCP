"""Immutable, versioned whole-document candidates reviewed before draft approval."""

import json

from .models import AdvancedSection, DocumentPlanInput, FoundationSection
from .render import digest
from .storage import dumps, event, now
from .topic import outline_issues


def candidate_hash(version, plan, dependencies, rendered):
    return digest(
        dumps(
            {
                "kind": "document_preview_v3",
                "version": version,
                "plan": plan,
                "dependencies": dependencies,
                "rendered_hash": digest(dumps(rendered)),
            }
        )
    )


class TopicService:
    @staticmethod
    def _preview_row(db, job_id, version=None):
        if version is None:
            return db.execute(
                "SELECT * FROM document_previews WHERE job_id=? ORDER BY version DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        return db.execute(
            "SELECT * FROM document_previews WHERE job_id=? AND version=?", (job_id, version)
        ).fetchone()

    def _preview_dependencies(self, db, job, report):
        sources = [
            dict(row)
            for row in db.execute(
                "SELECT source_id,content FROM sources WHERE job_id=? ORDER BY source_id",
                (job["id"],),
            )
        ]
        return {
            "base_hash": report["presentation_hash"],
            "research_revision": job["research_revision"],
            "sources_hash": digest(dumps(sources)),
        }

    def _preview_paths(self, job_id, version):
        directory = self.db.directory / "previews" / job_id
        return {
            "html": directory / f"v{version}.html",
            "markdown": directory / f"v{version}.md",
            "notion_markdown": directory / f"v{version}.notion.md",
        }

    @staticmethod
    def _preview_intact(row):
        return (
            candidate_hash(
                row["version"],
                json.loads(row["plan"]),
                json.loads(row["dependencies"]),
                json.loads(row["rendered"]),
            )
            == row["presentation_hash"]
        )

    def _topic_presentation(self, db, job, report):
        row = self._preview_row(db, job["id"])
        errors = list(report["errors"])
        warnings = list(report["warnings"])

        def error(code, message):
            errors.append({"code": code, "path": "document_preview", "message": message})

        if row is None:
            error("preview_required", "prepare_document_preview로 전체 문서 미리보기를 준비하세요.")
        elif not self._preview_intact(row):
            error("preview_integrity", "저장한 미리보기 묶음이 변경되었습니다.")
        else:
            if json.loads(row["dependencies"]) != self._preview_dependencies(db, job, report):
                error("stale_preview", "본문·자료·자산 변경 후 새 미리보기가 필요합니다.")
            rendered = json.loads(row["rendered"])
            warnings.extend(rendered.get("warnings", []))
            for key, path in self._preview_paths(job["id"], row["version"]).items():
                if not path.is_file() or path.read_text(encoding="utf-8") != rendered[key]:
                    error(
                        "preview_file_integrity",
                        "검토 파일이 없거나 변경되었습니다. 같은 구성안으로 미리보기를 재준비하세요.",
                    )
                    break
        return {
            **report,
            "errors": errors,
            "warnings": warnings,
            "valid": not errors,
            "preview_version": row["version"] if row else None,
            "presentation_hash": row["presentation_hash"] if row else None,
        }

    def prepare_document_preview(self, job_id, content: DocumentPlanInput, expected_version: int):
        from .presentation_service import atomic_text, fail, is_topic, profile_for
        from .render_v3 import render_topic

        if expected_version < 0:
            fail("invalid_version", "expected_version은 0 이상이어야 합니다.")
        with self.db.connect() as db:
            job = self._editable(db, job_id)
            if not is_topic(job):
                fail("profile_required", "전체 문서 후보는 study_topic_v3에서 준비하세요.")
            previous = self._preview_row(db, job_id)
            version = previous["version"] if previous else 0
            if expected_version != version:
                fail("version_conflict", "실제 읽은 미리보기 버전이 필요합니다.")
            validation = self._validation(db, job)
            report = self._presentation(db, job, include_preview=False)
            if not validation["valid"] or not report["valid"]:
                fail("validation_failed", dumps([*validation["issues"], *report["errors"]]))
            if (
                content.foundation_version != report["versions"]["foundation"]
                or content.advanced_version != report["versions"]["advanced"]
                or content.research_revision != job["research_revision"]
            ):
                fail("stale_preview", "실제 읽은 본문 버전과 조사 리비전이 필요합니다.")
            foundation = FoundationSection.model_validate_json(
                self._section(db, job_id, "foundation")["content"]
            )
            advanced = AdvancedSection.model_validate_json(
                self._section(db, job_id, "advanced")["content"]
            )
            outline = outline_issues(content, foundation, advanced)
            if outline["errors"]:
                fail("outline_invalid", dumps(outline["errors"]))
            for point in content.summary:
                section = foundation if point.section == "foundation" else advanced
                if not set(point.claim_ids) <= {
                    c.id for c in section.claims if c.verdict == "supported"
                }:
                    fail("unknown_claim", "요약은 검증된 해당 역할의 주장에 연결하세요.")
            dependencies = self._preview_dependencies(db, job, report)
            profile = profile_for(job)
            sources = self._source_map(db, job_id)
            assets = [
                {**asset, "path": str((self.root / asset["path"]).resolve())}
                for asset in report["assets"]
            ]
            plan = content.model_dump(mode="json")
            unchanged = (
                previous is not None
                and previous["plan"] == dumps(plan)
                and previous["dependencies"] == dumps(dependencies)
            )
            if unchanged and not self._preview_intact(previous):
                fail("preview_integrity", "기존 후보가 손상되었습니다.")
            cached = json.loads(previous["rendered"]) if unchanged else None
        # Rendering/file IO must never hold the database write lock.
        rendered = cached or render_topic(content, foundation, advanced, sources, profile, assets)
        if not unchanged:
            rendered["warnings"] = outline["warnings"]
            if not 3 <= len(content.summary) <= 5:
                rendered["warnings"].append(
                    {
                        "code": "summary_length",
                        "path": "summary",
                        "message": "핵심 요약은 3~5개를 권장합니다.",
                    }
                )
        with self.db.connect(write=True) as db:
            job = self._editable(db, job_id)
            latest = self._preview_row(db, job_id)
            current_version = latest["version"] if latest else 0
            current = self._presentation(db, job, include_preview=False)
            if (
                current_version != expected_version
                or not current["valid"]
                or dependencies != self._preview_dependencies(db, job, current)
            ):
                fail("version_conflict", "렌더링 중 본문·자료·자산·후보가 변경되었습니다.")
            if unchanged:
                if not self._preview_intact(latest):
                    fail("preview_integrity", "기존 후보가 손상되었습니다.")
            else:
                version += 1
                presentation_hash = candidate_hash(version, plan, dependencies, rendered)
                db.execute(
                    "INSERT INTO document_previews VALUES(?,?,?,?,?,?,?)",
                    (
                        job_id,
                        version,
                        dumps(plan),
                        dumps(dependencies),
                        dumps(rendered),
                        presentation_hash,
                        now(),
                    ),
                )
                self._invalidate(db, job_id)
                event(
                    db,
                    job_id,
                    "document_preview_prepared",
                    {"version": version, "presentation_hash": presentation_hash},
                )
        try:
            for key, path in self._preview_paths(job_id, version).items():
                atomic_text(path, rendered[key])
        except OSError as exc:
            return {
                "version": version,
                "export_error": str(exc),
                "idempotent": unchanged,
                "instructions": "후보는 저장되었습니다. 반환된 버전으로 같은 구성안을 재준비해 파일만 복구하세요.",
            }
        return {**self.get_document_preview(job_id, version), "idempotent": unchanged}

    def get_document_preview(self, job_id, version=None):
        from .presentation_service import fail

        with self.db.connect() as db:
            job = self._job(db, job_id)
            row = self._preview_row(db, job_id, version)
            if row is None:
                fail("preview_required", "지정한 미리보기가 없습니다.")
            if not self._preview_intact(row):
                fail("preview_integrity", "저장한 미리보기 묶음이 변경되었습니다.")
            current = self._presentation(db, job)
            rendered = json.loads(row["rendered"])
            return {
                "version": row["version"],
                "presentation_hash": row["presentation_hash"],
                "plan_hash": digest(row["plan"]),
                "content": json.loads(row["plan"]),
                "current": current["preview_version"] == row["version"] and current["valid"],
                "validation": current,
                "markdown": rendered["markdown"],
                "notion_markdown": rendered["notion_markdown"],
                "paths": {
                    key: str(path.resolve())
                    for key, path in self._preview_paths(job_id, row["version"]).items()
                },
            }
