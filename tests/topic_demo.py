"""Generate a LOCAL SYNTHETIC UI sample without creating a study or approval."""

import json
from pathlib import Path

from conftest import sample_source
from test_presentation_workflow import readable_sections
from test_topic_workflow import make_plan

from cs_study_mcp.assets import render_diagram
from cs_study_mcp.models import OutlineGroup, Placement, Visual
from cs_study_mcp.presentation import profile_snapshot
from cs_study_mcp.render_v3 import render_topic


def main():
    root = Path(__file__).resolve().parents[1]
    f, a = readable_sections()
    f.terms[0].name = "HTTP Request — 개발용 합성 샘플"
    f.terms[
        0
    ].definition = "이 페이지는 토글·코드·표·그림 배치를 확인하는 합성 테스트입니다. 원문 검증 또는 발행 승인을 받은 학습 문서가 아닙니다."
    f.principles[0].heading = "요청 메시지 구성"
    f.principles[0].key_point = "코드의 줄바꿈과 들여쓰기를 보존합니다."
    f.principles[
        0
    ].body = "개발용 HTTP 메시지 예시입니다.\n\n```http\nGET /example HTTP/1.1\nHost: example.org\nAccept: text/html\n```"
    spec = "sequenceDiagram\nparticipant B as 브라우저\nparticipant S as 서버\nB->>S: 합성 요청 예시\nS-->>B: 합성 응답 예시"
    f.visuals = [
        Visual(
            id="request-flow",
            kind="flow",
            title="요청·응답 배치 샘플",
            caption="개발용 가상 흐름이며 기술 주장 검증을 대신하지 않습니다.",
            alt_text="브라우저에서 서버로 요청하고 서버가 응답하는 두 화살표",
            diagram_spec=spec,
            claim_ids=["F1"],
            placement=Placement(section="foundation", after_id=f.principles[0].id),
        )
    ]
    metadata = render_diagram(root, spec)
    assets = [
        {
            **metadata,
            "section": "foundation",
            "visual_id": "request-flow",
            "path": str((root / metadata["path"]).resolve()),
        }
    ]
    plan = make_plan(f, a, title="HTTP Request · v3 개발용 합성 미리보기")
    plan.outline[0].children[0].children = [
        OutlineGroup(
            id="message",
            title="메시지·표·그림 펼치기",
            children=plan.outline[0].children[0].children,
        )
    ]
    rendered = render_topic(
        plan, f, a, {"S1": sample_source()}, profile_snapshot("study_topic_v3"), assets
    )
    directory = root / ".cs-study" / "previews" / "topic-v3-demo"
    directory.mkdir(parents=True, exist_ok=True)
    for key, name in (
        ("markdown", "document.md"),
        ("notion_markdown", "notion.md"),
    ):
        (directory / name).write_text(rendered[key], encoding="utf-8")
    (directory / "manifest.json").write_text(
        json.dumps({"synthetic": True, "assets": assets}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(directory / "document.md")


if __name__ == "__main__":
    main()
