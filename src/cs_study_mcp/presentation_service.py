"""Versioned presentation artifacts; no model calls or remote writes."""

import json
import os
import tempfile
from pathlib import Path

from .models import AdvancedSection, FoundationSection
from .presentation import document_blueprint, presentation_issues, profile_snapshot
from .render import digest
from .storage import dumps, event, now
from .topic_service import TopicService
from .visual_acquisition_service import VisualAcquisitionService
from .visual_assessment import assessment_policy


def fail(code, message):
    from .service import WorkflowError

    raise WorkflowError(code, message)


def profile_for(job):
    value = job["presentation_profile"]
    return json.loads(value) if value else profile_snapshot("legacy_v1")


def is_readable(job):
    return profile_for(job)["name"] in {"study_readable_v2", "study_topic_v3"}


def is_topic(job):
    return profile_for(job)["name"] == "study_topic_v3"


def atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class PresentationService(TopicService, VisualAcquisitionService):
    def get_document_blueprint(self, job_id, role):
        with self.db.connect() as db:
            profile = profile_for(self._job(db, job_id))
            return {**document_blueprint(profile, role), "profile_hash": digest(dumps(profile))}

    def register_visual_asset(self, job_id: str, path: str) -> dict:
        from .assets import store_asset

        with self.db.connect() as db:
            if not is_readable(self._editable(db, job_id)):
                fail("legacy_profile", "시각 자산은 새 형식의 작업에 등록하세요.")
        asset = store_asset(self.root, Path(path))
        with self.db.connect(write=True) as db:
            self._editable(db, job_id)
            self._store_asset(db, job_id, asset)
        return asset

    @staticmethod
    def _store_asset(db, job_id, asset):
        db.execute(
            "INSERT OR IGNORE INTO assets(job_id,hash,metadata,created_at) VALUES(?,?,?,?)",
            (job_id, asset["hash"], dumps(asset), now()),
        )

    def _presentation(self, db, job, *, include_preview=True):
        from .assets import verify_asset

        profile = profile_for(job)
        rows = {kind: self._section(db, job["id"], kind) for kind in ("foundation", "advanced")}
        versions = {kind: row["version"] if row else 0 for kind, row in rows.items()}
        if profile["name"] == "legacy_v1":
            return {
                "valid": True,
                "profile": profile,
                "versions": versions,
                "errors": [],
                "warnings": [],
                "manual_review": [],
                "assets": [],
                "presentation_hash": None,
            }
        f = (
            FoundationSection.model_validate_json(rows["foundation"]["content"])
            if rows["foundation"]
            else None
        )
        a = (
            AdvancedSection.model_validate_json(rows["advanced"]["content"])
            if rows["advanced"]
            else None
        )
        report = presentation_issues(f, a, self._source_map(db, job["id"]), profile)
        assets = []
        for kind, section in (("foundation", f), ("advanced", a)):
            if section is None:
                continue
            for visual in section.visuals:
                saved = db.execute(
                    "SELECT metadata FROM section_assets WHERE job_id=? AND kind=? AND version=? AND visual_id=?",
                    (job["id"], kind, versions[kind], visual.id),
                ).fetchone()
                asset = json.loads(saved["metadata"]) if saved else None
                if not asset:
                    report["errors"].append(
                        {
                            "code": "missing_asset",
                            "path": f"{kind}.{visual.id}",
                            "message": "prepare_visual_assets로 검토용 자산을 준비하세요.",
                        }
                    )
                elif not verify_asset(self.root, asset):
                    report["errors"].append(
                        {
                            "code": "asset_integrity",
                            "path": f"{kind}.{visual.id}",
                            "message": "자산이 없거나 승인된 바이트와 다릅니다.",
                        }
                    )
                else:
                    assets.append(asset)
        manifest = {
            "profile": profile,
            "versions": versions,
            "section_hashes": {k: row["content_hash"] if row else None for k, row in rows.items()},
            "assets": [{k: v for k, v in asset.items() if k != "path"} for asset in assets],
        }
        evidence = {}
        if assessment_policy(profile):
            evidence = self._assessment_evidence(db, job, (f, a))
            report["errors"].extend(evidence["errors"])
            manifest["visual_acquisitions"] = evidence["acquisitions"]
        result = {
            **report,
            "valid": not report["errors"],
            "versions": versions,
            "profile": profile,
            "assets": assets,
            "presentation_hash": digest(dumps(manifest)),
        }
        if evidence:
            result["visual_assessments"] = evidence["visual_assessments"]
        if is_topic(job) and include_preview:
            return self._topic_presentation(db, job, result)
        return result

    def validate_presentation(self, job_id):
        with self.db.connect() as db:
            return self._presentation(db, self._job(db, job_id))

    def prepare_visual_assets(self, job_id, expected_versions):
        from .assets import render_diagram, verify_asset

        with self.db.connect() as db:
            job = self._editable(db, job_id)
            if not is_readable(job):
                fail("legacy_profile", "기존 작업에는 시각 자료를 추가할 수 없습니다.")
            rows = {k: self._section(db, job_id, k) for k in ("foundation", "advanced")}
            versions = {k: r["version"] if r else 0 for k, r in rows.items()}
            if expected_versions != versions:
                fail("version_conflict", "실제 읽은 양쪽 버전을 전달하세요.")
            tasks = []
            for kind, model in (("foundation", FoundationSection), ("advanced", AdvancedSection)):
                if rows[kind] is None:
                    continue
                section = model.model_validate_json(rows[kind]["content"])
                for visual in section.visuals:
                    saved = db.execute(
                        "SELECT metadata FROM section_assets WHERE job_id=? AND kind=? AND version=? AND visual_id=?",
                        (job_id, kind, versions[kind], visual.id),
                    ).fetchone()
                    if saved and verify_asset(self.root, json.loads(saved["metadata"])):
                        continue
                    source = None
                    if visual.kind == "screenshot":
                        source = db.execute(
                            "SELECT metadata FROM assets WHERE job_id=? AND hash=?",
                            (job_id, visual.asset_hash),
                        ).fetchone()
                    tasks.append(
                        (kind, visual, json.loads(source["metadata"]) if source else None, saved)
                    )
        results, errors = [], []
        # Rendering never holds the SQLite write lock. A second version check precedes commit.
        for kind, visual, imported, saved in tasks:
            try:
                if visual.kind == "screenshot":
                    if not imported or not verify_asset(self.root, imported):
                        raise ValueError("등록된 스크린샷 자산이 필요합니다.")
                    asset = dict(imported)
                    asset["spec_hash"] = digest(dumps(visual.model_dump(mode="json")))
                    direct = visual.screenshot and visual.screenshot.capture_method == "direct_capture"
                    asset["renderer_fingerprint"] = (
                        "direct-capture" if direct else "original-public-screenshot"
                    )
                    if visual.screenshot and visual.screenshot.image_url:
                        asset["remote_url"] = str(visual.screenshot.image_url)
                else:
                    if not visual.diagram_spec:
                        raise ValueError("그림 정의가 없습니다.")
                    asset = render_diagram(self.root, visual.diagram_spec, visual.kind)
                asset.update(
                    section=kind,
                    visual_id=visual.id,
                    kind=visual.kind,
                    caption=visual.caption,
                    alt_text=visual.alt_text,
                )
                if saved and json.loads(saved["metadata"])["hash"] != asset["hash"]:
                    raise ValueError(
                        "같은 버전 자산의 해시가 달라졌습니다. 새 부분 문서 버전이 필요합니다."
                    )
                results.append(asset)
            except (OSError, ValueError, RuntimeError) as exc:
                errors.append(
                    {"code": "render_failed", "path": f"{kind}.{visual.id}", "message": str(exc)}
                )
        with self.db.connect(write=True) as db:
            self._editable(db, job_id)
            actual = {k: self._section(db, job_id, k) for k in versions}
            if {k: row["version"] if row else 0 for k, row in actual.items()} != versions:
                fail(
                    "version_conflict",
                    "렌더링 중 부분 문서가 바뀌었습니다. 최신 버전으로 다시 준비하세요.",
                )
            inserted = []
            for asset in results:
                previous = db.execute(
                    "SELECT metadata FROM section_assets WHERE job_id=? AND kind=? AND version=? AND visual_id=?",
                    (job_id, asset["section"], versions[asset["section"]], asset["visual_id"]),
                ).fetchone()
                if previous:
                    if json.loads(previous["metadata"]) != asset:
                        fail(
                            "asset_conflict",
                            "같은 부분 문서 버전에 다른 자산이 이미 연결되어 있습니다.",
                        )
                    continue
                self._store_asset(db, job_id, asset)
                db.execute(
                    "INSERT INTO section_assets VALUES(?,?,?,?,?)",
                    (
                        job_id,
                        asset["section"],
                        versions[asset["section"]],
                        asset["visual_id"],
                        dumps(asset),
                    ),
                )
                inserted.append(asset["hash"])
            if inserted:
                self._invalidate(db, job_id)
                event(
                    db, job_id, "visual_assets_prepared", {"versions": versions, "hashes": inserted}
                )
        with self.db.connect() as db:
            job = self._job(db, job_id)
            report = self._presentation(db, job, include_preview=not is_topic(job))
            if is_topic(job):
                report["next_step"] = "prepare_document_preview로 전체 배치를 준비한 뒤 교차 검토하세요."
        report["errors"].extend(errors)
        report["valid"] = not report["errors"]
        return report

    def _require_presentation(self, db, job):
        report = self._presentation(db, job)
        if not report["valid"]:
            fail("presentation_invalid", dumps(report["errors"]))
        return report

    def _check_bundle(self, db, job, draft):
        if not is_readable(job):
            return
        report = self._require_presentation(db, job)
        if not draft["manifest"] or not draft["bundle_hash"]:
            fail("bundle_required", "시각 자료 승인 묶음이 없습니다.")
        manifest = json.loads(draft["manifest"])
        if (
            digest(dumps(manifest)) != draft["bundle_hash"]
            or manifest["presentation_hash"] != report["presentation_hash"]
            or manifest["markdown_hash"] != digest(draft["markdown"])
            or manifest["notion_hash"] != digest(draft["notion_markdown"])
            or manifest["preview_hash"] != digest(draft["preview_html"])
        ):
            fail("bundle_changed", "승인 대상의 본문·그림·배치가 변경되었습니다.")
        if is_topic(job):
            from .models import DraftInput

            candidate = self._preview_row(db, job["id"])
            rendered = json.loads(candidate["rendered"])
            plan = json.loads(candidate["plan"])
            expected_content = {key: plan[key] for key in DraftInput.model_fields}
            if (json.loads(draft["content"]) != expected_content
                    or manifest.get("hash_kind") != "presentation_bundle_v3"
                    or manifest.get("composition_version") != candidate["version"]
                    or manifest.get("plan_hash") != digest(candidate["plan"])
                    or any(draft[key] != rendered[value] for key, value in (
                        ("markdown", "markdown"), ("notion_markdown", "notion_markdown"),
                        ("preview_html", "html")))
                    or manifest.get("blocks") != rendered["blocks"]):
                fail("bundle_changed", "검토한 미리보기와 승인 초안이 다릅니다.")

    def validate_publication_bundle(self, job_id):
        with self.db.connect() as db:
            job = self._job(db, job_id)
            self._check_bundle(db, job, self._draft(db, job))

    def record_publication_asset(self, job_id, attempt_id, asset_hash, remote_ref, remote_url):
        """Record an observed provider upload receipt, never grant upload authorization."""
        from urllib.parse import urlsplit

        parsed = urlsplit(remote_url)
        if (not remote_ref.strip() or parsed.scheme not in ("https", "file-upload")
                or not parsed.netloc or parsed.username or parsed.password
                or any(c.isspace() or c in '<>\"()[]' for c in remote_url)):
            fail("invalid_receipt", "실제 업로드 응답의 안정 참조와 안전한 HTTPS/file-upload URL이 필요합니다.")
        with self.db.connect(write=True) as db:
            pub = db.execute("SELECT * FROM publications WHERE job_id=?", (job_id,)).fetchone()
            if not pub or pub["attempt_id"] != attempt_id or pub["status"] == "completed":
                fail("unknown_attempt", "활성 발행 시도가 필요합니다.")
            draft = db.execute(
                "SELECT * FROM drafts WHERE job_id=? AND version=?", (job_id, pub["draft_version"])
            ).fetchone()
            manifest = json.loads(draft["manifest"] or "{}")
            if asset_hash not in {x["hash"] for x in manifest.get("assets", [])}:
                fail("unknown_asset", "승인된 자산만 업로드할 수 있습니다.")
            receipt = {"hash": asset_hash, "remote_ref": remote_ref, "remote_url": remote_url}
            previous = db.execute(
                "SELECT receipt FROM publication_assets WHERE job_id=? AND asset_hash=?",
                (job_id, asset_hash),
            ).fetchone()
            if previous:
                if json.loads(previous["receipt"]) != receipt:
                    fail(
                        "receipt_conflict",
                        "기존 업로드 영수증을 바꿀 수 없습니다. 기존 자산을 조회하세요.",
                    )
                return {**receipt, "idempotent": True}
            db.execute(
                "INSERT INTO publication_assets VALUES(?,?,?,?)",
                (job_id, asset_hash, dumps(receipt), now()),
            )
            event(db, job_id, "asset_uploaded", receipt)
            return {**receipt, "idempotent": False}

    def get_publication_payload(self, job_id, attempt_id):
        """Read resolved payload without reserving or granting another page creation."""
        import re

        with self.db.connect() as db:
            job = self._job(db, job_id)
            publication = db.execute(
                "SELECT * FROM publications WHERE job_id=?", (job_id,)
            ).fetchone()
            if not publication or publication["attempt_id"] != attempt_id:
                fail("unknown_attempt", "기존 발행 시도 ID가 필요합니다.")
            draft = db.execute(
                "SELECT * FROM drafts WHERE job_id=? AND version=?",
                (job_id, publication["draft_version"]),
            ).fetchone()
            self._check_bundle(db, job, draft)
            body = draft["notion_markdown"] or draft["markdown"].split("\n\n", 1)[1]
            receipts = self._receipts(db, job_id)
            for receipt in receipts:
                body = body.replace("asset://" + receipt["hash"], receipt["remote_url"])
            return {
                "attempt_id": attempt_id,
                "markdown": body,
                "required_uploads": sorted(set(re.findall(r"asset://([a-f0-9]{64})", body))),
                "asset_receipts": receipts,
                "bundle_hash": draft["bundle_hash"],
                "instructions": "읽기 결과는 새 생성 권한이 아닙니다. 최초 create_page를 받은 동일 실행에서만 생성하고, 재시작 시 prepare_publication의 복구 action을 따르세요.",
            }

    @staticmethod
    def _receipts(db, job_id):
        return [
            json.loads(row["receipt"])
            for row in db.execute(
                "SELECT receipt FROM publication_assets WHERE job_id=? ORDER BY asset_hash",
                (job_id,),
            )
        ]
