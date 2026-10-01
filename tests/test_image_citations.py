"""Image provenance and reference placement using synthetic assets, never live Notion."""

from copy import deepcopy

import pytest
from conftest import PAGE_ID, PARENT_ID, draft_input, sample_source
from pydantic import ValidationError
from test_assets import PNG
from test_presentation_workflow import approve_v2, readable_sections, review_and_draft
from test_topic_workflow import make_plan, promote, review

from cs_study_mcp.models import Placement, PublicationInput, ScreenshotSource, Visual
from cs_study_mcp.presentation import profile_snapshot
from cs_study_mcp.render_v2 import render_readable
from cs_study_mcp.render_v3 import render_topic
from cs_study_mcp.service import StudyService
from cs_study_mcp.storage import dumps


def capture(**overrides):
    return ScreenshotSource.model_validate({
        "source_id": "S1",
        "capture_method": "direct_capture",
        "capture_target": "local demo: python example.py",
        "captured_at": "2026-10-01T12:30:00+09:00",
        "capture_environment": "Windows 11 / Python 3.12",
        "locator": "실습 결과 창",
        "accessed_at": sample_source().accessed_at,
        "usage_note": "합성 시험 이미지, 외부 계정 사용 없음",
        **overrides,
    })


def screenshot(provenance, asset_hash="a" * 64, after_id=None):
    return Visual(
        id="screen", kind="screenshot", title="예시 화면",
        caption="화면에서 동작 결과를 확인한다.", alt_text="실습 결과 화면",
        claim_ids=["F1"], asset_hash=asset_hash, screenshot=provenance,
        placement=Placement(section="foundation", after_id=after_id),
    )


@pytest.mark.parametrize("missing", ["capture_target", "captured_at", "capture_environment"])
def test_direct_capture_requires_reproducible_provenance(missing):
    with pytest.raises(ValidationError, match="대상·captured_at·capture_environment"):
        capture(**{missing: None})


def test_web_capture_records_url_without_requiring_local_target():
    value = capture(capture_target=None, capture_url="https://example.org/demo")
    assert str(value.capture_url) == "https://example.org/demo"
    assert value.image_url is None
    with pytest.raises(ValidationError):
        capture(captured_at="2026-10-01T12:30:00")
    with pytest.raises(ValidationError):
        capture(image_url="https://example.org/image.png")


def test_public_image_still_requires_url_and_preserves_historical_serialization():
    original = {
        "source_id": "S1", "image_url": "https://example.org/image.png",
        "locator": "figure 1", "accessed_at": "2026-10-01T00:00:00Z",
        "product_version": None, "usage_note": "인용 조건 확인",
    }
    value = ScreenshotSource.model_validate(original)
    assert value.model_dump(mode="json") == original
    assert ScreenshotSource.model_validate_json(dumps(original)) == value
    with pytest.raises(ValidationError, match="image_url"):
        ScreenshotSource.model_validate({**original, "image_url": None})
    with pytest.raises(ValidationError, match="capture_method"):
        ScreenshotSource.model_validate({**original, "capture_target": "demo"})


@pytest.mark.parametrize("profile_name", ["study_readable_v2", "study_topic_v3"])
@pytest.mark.parametrize("direct", [False, True])
def test_images_are_embedded_and_all_citations_are_only_in_final_references(
    tmp_path, profile_name, direct,
):
    f, a = readable_sections()
    # This source is used only by the image, so hiding inline references must not lose it.
    image_source = sample_source("S2").model_copy(update={"title": "이미지 근거"})
    provenance = capture(source_id="S2") if direct else ScreenshotSource(
        source_id="S2", image_url="https://example.org/image.png", locator="figure 2",
        accessed_at=image_source.accessed_at, usage_note="이용 조건 확인", product_version="v1",
    )
    f.visuals = [screenshot(provenance, after_id=f.examples[0].id)]
    f.comparisons[0].rows[0].claim_ids = ["F1"]
    path = tmp_path / "screen.png"
    path.write_bytes(PNG)
    assets = [{"section": "foundation", "visual_id": "screen", "hash": "a" * 64,
               "path": str(path)}]
    sources = {"S1": sample_source(), "S2": image_source}
    profile = profile_snapshot(profile_name)
    result = (
        render_topic(make_plan(f, a), f, a, sources, profile, assets)
        if profile_name == "study_topic_v3"
        else render_readable(draft_input(), f, a, sources, profile, assets)
    )
    for key in ("markdown", "notion_markdown"):
        body, references = result[key].split("## 참고 문헌", 1)
        assert "![실습 결과 화면](" in body
        assert body.index(f.examples[0].title) < body.index("![실습 결과 화면](")
        assert "[1]" not in body and "[2]" not in body
        assert "https://example.org/test-fixture" not in body
        assert "확인된 사실의 근거" not in body and "이용 조건" not in body
        assert "[1: 모의 원문]" in references and "[2: 이미지 근거]" in references
        assert "이용 조건" in references and "이미지: 예시 화면" in references
        if direct:
            assert "Windows 11 / Python 3.12" in references
            assert "2026-10-01T12:30:00+09:00" in references
            assert "local demo: python example.py" in references
        else:
            assert "[원본 이미지](https://example.org/image.png)" in references
    assert path.as_uri() in result["markdown"]
    assert ("asset://" + "a" * 64 in result["notion_markdown"]) == direct
    # Hiding references does not remove the evidence used by validation.
    assert f.visuals[0].screenshot.source_id == "S2"
    assert f.claims[0].source_ids == ["S1"]


@pytest.mark.parametrize("profile_name", ["study_readable_v2", "study_topic_v3"])
def test_direct_capture_upload_resume_and_readback_keep_the_approved_image(service, profile_name):
    actual = StudyService(service.root)
    job_id = actual.create_study("합성 직접 캡처 시험", presentation_profile=profile_name)["job_id"]
    actual.save_research(job_id, [sample_source()])
    path = actual.root / "screen.png"
    path.write_bytes(PNG)
    asset = actual.register_visual_asset(job_id, str(path))
    f, a = readable_sections()
    f.visuals = [screenshot(capture(), asset["hash"], f.examples[0].id)]
    actual.save_knowledge_section(job_id, f, 0)
    actual.save_knowledge_section(job_id, a, 0)
    prepared = actual.prepare_visual_assets(job_id, {"foundation": 1, "advanced": 1})
    assert prepared["valid"], prepared
    report = actual.validate_presentation(job_id)
    assert "remote_url" not in report["assets"][0]
    assert report["assets"][0]["renderer_fingerprint"] == "direct-capture"
    if profile_name == "study_topic_v3":
        preview = actual.prepare_document_preview(job_id, make_plan(f, a), 0)
        review(actual, job_id, preview)
        draft = promote(actual, job_id, preview)
    else:
        draft = review_and_draft(actual, job_id)
    approve_v2(actual, job_id, draft)
    approved = actual.get_study(job_id)
    intent = actual.prepare_publication(job_id, draft["version"])
    assert intent["required_uploads"] == [asset["hash"]]
    receipt = actual.record_publication_asset(
        job_id, intent["attempt_id"], asset["hash"], "synthetic-upload-1",
        "https://cdn.example.org/synthetic-screen.png",
    )
    assert not receipt["idempotent"]
    restarted = StudyService(actual.root)
    assert restarted.prepare_publication(job_id, draft["version"])["action"] == "inspect_before_retry"
    payload = restarted.get_publication_payload(job_id, intent["attempt_id"])
    assert not payload["required_uploads"] and "asset://" not in payload["markdown"]
    assert "![실습 결과 화면](https://cdn.example.org/synthetic-screen.png)" in payload["markdown"]
    assert restarted.record_publication_asset(
        job_id, intent["attempt_id"], asset["hash"], receipt["remote_ref"], receipt["remote_url"],
    )["idempotent"]
    restarted.record_publication(job_id, PublicationInput(
        attempt_id=intent["attempt_id"], outcome="page_created", page_id=PAGE_ID,
    ))
    base = dict(
        attempt_id=intent["attempt_id"], outcome="verified", page_id=PAGE_ID,
        observed_title=intent["title"], observed_parent_page_id=PARENT_ID,
        observed_markdown=payload["markdown"],
    )
    # URL equality alone never proves the image is the approved bytes.
    assert restarted.record_publication(job_id, PublicationInput(**base))["status"] == "needs_attention"
    assert restarted.record_publication(job_id, PublicationInput(
        **base, observed_assets=[{"remote_ref": receipt["remote_ref"], "remote_url": receipt["remote_url"]}],
    ))["status"] == "completed"
    completed = restarted.get_study(job_id)
    assert completed["draft"] == approved["draft"] and completed["reviews"] == approved["reviews"]


@pytest.mark.parametrize("profile_name", ["study_readable_v2", "study_topic_v3"])
def test_existing_snapshot_retains_inline_citations_and_approval(service, monkeypatch, profile_name):
    old = deepcopy(profile_snapshot(profile_name))
    old.pop("citation_style")
    with monkeypatch.context() as context:
        context.setattr("cs_study_mcp.service.profile_snapshot", lambda _: old)
        actual = StudyService(service.root)
        job_id = actual.create_study("기존 표기 fixture", presentation_profile=profile_name)["job_id"]
    actual.save_research(job_id, [sample_source()])
    f, a = readable_sections()
    actual.save_knowledge_section(job_id, f, 0)
    actual.save_knowledge_section(job_id, a, 0)
    if profile_name == "study_topic_v3":
        preview = actual.prepare_document_preview(job_id, make_plan(f, a), 0)
        review(actual, job_id, preview)
        draft = promote(actual, job_id, preview)
    else:
        draft = review_and_draft(actual, job_id)
    approve_v2(actual, job_id, draft)
    before = actual.get_study(job_id)
    assert "[1](https://example.org/test-fixture)" in draft["markdown"].split("## 참고자료")[0]
    other = actual.create_study("새 표기 fixture", presentation_profile=profile_name)
    assert other["presentation_profile"]["citation_style"] == "references_only"
    assert actual.get_study(job_id) == before
    assert actual.get_document_blueprint(job_id, "foundation")["profile"] == old
    actual.validate_publication_bundle(job_id)
