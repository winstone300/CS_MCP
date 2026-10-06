"""Main-owned immutable acquisition receipts, separate from authored sections."""

import json

from .assets import verify_asset
from .models import AdvancedSection, FoundationSection
from .storage import dumps, event, now
from .visual_assessment import (
    acquisition_hash,
    assessment_policy,
    request_hash,
    target_hash,
    target_items,
)


class VisualAcquisitionService:
    @staticmethod
    def _acquisition_records(db, job_id):
        records = []
        for row in db.execute(
            "SELECT * FROM visual_acquisitions WHERE job_id=? ORDER BY id", (job_id,)
        ):
            content = json.loads(row["content"])
            records.append(
                {
                    **content,
                    "target_hash": row["target_hash"],
                    "request_hash": row["request_hash"],
                    "hash": row["content_hash"],
                    "intact": acquisition_hash(content, row["target_hash"], row["request_hash"])
                    == row["content_hash"],
                }
            )
        return records

    def get_visual_acquisitions(self, job_id):
        with self.db.connect() as db:
            self._job(db, job_id)
            return {
                "results": self._acquisition_records(db, job_id),
                "registered_assets": [
                    json.loads(row["metadata"])
                    for row in db.execute(
                        "SELECT metadata FROM assets WHERE job_id=? ORDER BY hash", (job_id,)
                    )
                ],
            }

    def record_visual_acquisition(self, job_id, content):
        from .presentation_service import fail, profile_for

        serialized = dumps(content.model_dump(mode="json"))
        with self.db.connect(write=True) as db:
            job = self._editable(db, job_id)
            if not assessment_policy(profile_for(job)):
                fail(
                    "visual_policy_required",
                    "새 시각 자료 판단 정책이 있는 작업에만 결과를 기록하세요.",
                )
            previous = db.execute(
                "SELECT * FROM visual_acquisitions WHERE job_id=? AND result_id=?",
                (job_id, content.result_id),
            ).fetchone()
            if previous:
                if previous["content"] != serialized:
                    fail(
                        "acquisition_conflict",
                        "확보 결과 ID는 불변입니다. 새 결과에는 새 ID를 사용하세요.",
                    )
                if (
                    acquisition_hash(
                        json.loads(serialized), previous["target_hash"], previous["request_hash"]
                    )
                    != previous["content_hash"]
                ):
                    fail("acquisition_integrity", "저장된 확보 결과가 변경되었습니다.")
                return {
                    "result_id": content.result_id,
                    "hash": previous["content_hash"],
                    "idempotent": True,
                }
            row = self._section(db, job_id, content.section)
            if row is None or row["version"] != content.expected_section_version:
                fail(
                    "version_conflict", "실제 읽은 최신 부분 문서 버전으로 확보 결과를 기록하세요."
                )
            model = FoundationSection if content.section == "foundation" else AdvancedSection
            section = model.model_validate_json(row["content"])
            matches = [
                item for item in section.visual_assessments if item.id == content.assessment_id
            ]
            if len(matches) != 1 or matches[0].item_id not in target_items(section):
                fail("unknown_assessment", "확보 대상 평가와 본문 항목을 먼저 저장하세요.")
            assessment = matches[0]
            if content.outcome == "acquired":
                saved = db.execute(
                    "SELECT metadata FROM assets WHERE job_id=? AND hash=?",
                    (job_id, content.asset_hash),
                ).fetchone()
                if saved is None or not verify_asset(self.root, json.loads(saved["metadata"])):
                    fail("asset_integrity", "현재 작업에 등록된 실제 자산이 필요합니다.")
                if content.screenshot.source_id not in self._source_map(db, job_id):
                    fail("unknown_source", "확보한 이미지의 근거 출처를 먼저 등록하세요.")
            target = target_hash(target_items(section)[assessment.item_id])
            request = request_hash(assessment)
            result_hash = acquisition_hash(json.loads(serialized), target, request)
            db.execute(
                "INSERT INTO visual_acquisitions(job_id,result_id,content,content_hash,target_hash,request_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                (job_id, content.result_id, serialized, result_hash, target, request, now()),
            )
            self._invalidate(db, job_id)
            event(
                db,
                job_id,
                "visual_acquisition_recorded",
                {"result_id": content.result_id, "hash": result_hash},
            )
            return {"result_id": content.result_id, "hash": result_hash, "idempotent": False}

    def _assessment_evidence(self, db, job, sections):
        """Freeze all relevant receipts; a newer unreflected result blocks promotion."""
        records = self._acquisition_records(db, job["id"])
        by_id = {record["result_id"]: record for record in records}
        errors, review_data = [], []
        for section in sections:
            if section is None:
                continue
            items = target_items(section)
            visuals = {item.id: item for item in section.visuals}
            for assessment in section.visual_assessments:
                linked = []

                def error(code, message, path=f"{section.kind}.visual_assessments.{assessment.id}"):
                    errors.append({"code": code, "path": path, "message": message})

                for rid in assessment.result_ids:
                    record = by_id.get(rid)
                    if (
                        record is None
                        or record["section"] != section.kind
                        or record["assessment_id"] != assessment.id
                    ):
                        error(
                            "unknown_acquisition_result",
                            "같은 작업·평가의 실제 확보 결과에 연결하세요.",
                        )
                        continue
                    linked.append(record)
                    if not record["intact"]:
                        error("acquisition_integrity", "연결한 확보 결과의 내용이 변경되었습니다.")
                    if (
                        assessment.item_id not in items
                        or record["target_hash"] != target_hash(items[assessment.item_id])
                        or record["request_hash"] != request_hash(assessment)
                    ):
                        error(
                            "stale_acquisition_result",
                            "대상 본문·요청 변경 후 확보 결과의 적합성을 다시 확인해 기록하세요.",
                        )
                    if record["outcome"] == "acquired":
                        saved = db.execute(
                            "SELECT metadata FROM assets WHERE job_id=? AND hash=?",
                            (job["id"], record["asset_hash"]),
                        ).fetchone()
                        if saved is None or not verify_asset(
                            self.root, json.loads(saved["metadata"])
                        ):
                            error(
                                "asset_integrity",
                                "확보 결과의 자산이 없거나 바이트가 변경되었습니다.",
                            )
                        if record["screenshot"]["source_id"] not in self._source_map(db, job["id"]):
                            error("unknown_source", "확보 결과의 이미지 근거 출처가 없습니다.")
                relevant = [
                    record
                    for record in records
                    if record["section"] == section.kind
                    and record["assessment_id"] == assessment.id
                ]
                if relevant and relevant[-1]["result_id"] not in assessment.result_ids:
                    error(
                        "unreflected_acquisition_result",
                        "메인의 최신 확보 결과를 읽고 작성 부분에 반영하세요.",
                    )
                if assessment.selected_kind == "image":
                    for vid in assessment.visual_ids:
                        visual = visuals.get(vid)
                        if visual and not any(
                            record["outcome"] == "acquired"
                            and record["asset_hash"] == visual.asset_hash
                            and record["screenshot"]
                            == (
                                visual.screenshot.model_dump(mode="json")
                                if visual.screenshot
                                else None
                            )
                            for record in linked
                        ):
                            error(
                                "acquisition_visual_mismatch",
                                "그림의 자산·출처·캡처 정보가 실제 확보 결과와 일치해야 합니다.",
                            )
                review_data.append(
                    {
                        "section": section.kind,
                        "assessment": assessment.model_dump(mode="json"),
                        "acquisition_results": linked,
                    }
                )
        return {"errors": errors, "visual_assessments": review_data, "acquisitions": records}
