"""Generate dedicated project config; preserve user-edited role prompts and model overrides."""

import argparse
import re
import sys
import tomllib
from pathlib import Path

from cs_study_mcp.server import TOOLS_BY_ROLE

DESCRIPTIONS = {
    "research": "CS 주제의 웹 원문과 실제 사례 근거를 수집하는 정보검색 에이전트",
    "foundation": "CS 기본 개념·용어·원리·쉬운 예시를 정리하고 심화 결과를 검토하는 에이전트",
    "advanced": "CS 심화 동작·장단점·실제 사례를 분석하고 기초 결과를 검토하는 에이전트",
    "notion_writer": "사용자가 승인한 학습 초안만 Notion에 발행하고 재조회하는 에이전트",
}
INSTRUCTIONS = {
    "research": """get_study로 고정된 규칙과 조사 범위를 확인한다.
Codex 웹 검색과 원문 열기만으로 조사한다. 검색 요약만으로 original_checked=true를 기록하지 않는다.
save_research에 source ID, URL, 제목, 종류, ISO 확인 시각, 발췌와 위치를 저장한다.
source ID는 작업 내 고유하며 변경할 수 없다. 분담 조사에서는 메인이 준 접두사를 사용한다.
기초 근거와 실제 적용 사례 근거를 구분해 메인에게 반환한다.
추가 검색 회차는 메인이 승인한 request_research_followup 결과를 따른다.
Notion 읽기/쓰기, 부분 문서 작성, 사용자 승인 기록은 하지 않는다.""",
    "foundation": """get_study와 작업의 rules_text를 읽고 기초 범위와 질문 수를 확인한다.
기존 출처만으로 정의·필요성·예시를 담은 용어집과 기본 원리를 먼저 저장한다.
save_knowledge_section에는 kind=foundation, expected_version=현재 버전(최초 0)을 사용한다.
완성 시 목표·선수지식·원리·예시·흔한 오해·검증된 주장·기초 질문을 포함한다.
질문 배분은 get_question_blueprint(role=foundation, question_count=작업 전체 질문 수)를 따른다.
추가 근거가 필요하면 직접 검색하지 말고 메인에 요청한다.
심화 에이전트에 최신 기초 버전을 알려준다. foundation_version이 달라지면 심화도 갱신해야 한다.
advanced 결과의 용어·전제·이해 연결·실제사례 근거를 교차 검토한다.
record_cross_review의 reviewer=foundation으로 자신의 의견만 기록한다.
검토한 양쪽 버전과 실제 읽은 research_revision을 함께 전달한다.
상대 문서는 수정하지 않는다. Notion 도구·승인 도구를 사용하지 않는다.""",
    "advanced": """get_study와 get_knowledge_section으로 기초 버전·출처·규칙을 확인한다.
기초 용어를 출발점으로 내부 동작·성능 전제·트레이드오프와 실제 사례를 작성한다.
save_knowledge_section에는 kind=advanced, foundation_version=참조한 기초 버전,
expected_version=현재 심화 버전(최초 0)을 사용한다.
실제 사례 최소 1개를 문제→기술→선택이유→확인된 결과→한계로 설명한다.
원문이 확인한 사실과 interpretation을 분리한다. 수치나 채택 사실을 추측하지 않는다.
질문 배분은 get_question_blueprint(role=advanced, question_count=작업 전체 질문 수)를 따른다.
추가 근거는 메인에 요청한다. 기초 버전이 바뀌면 최신 내용을 확인하고 자신의 참조 버전을 갱신한다.
foundation의 부정확한 단순화·생략된 전제·질문 중복을 검토한다.
record_cross_review의 reviewer=advanced로 자신의 의견만 기록한다.
검토한 양쪽 버전과 실제 읽은 research_revision을 함께 전달한다.
상대 문서는 수정하지 않는다. Notion 도구·승인 도구를 사용하지 않는다.""",
    "notion_writer": """get_study로 실제 사용자 승인, 초안 버전, 대상 페이지를 먼저 확인한다.
Notion 도구 목록/접근과 대상 읽기를 확인한 뒤 prepare_publication을 한 번 호출한다.
create_page일 때만 반환된 parent_page_id 아래 제목·Markdown을 변경 없이 새 페이지로 만든다.
Notion 생성 응답에서 page ID를 받으면 즉시 record_publication(outcome=page_created)으로 저장한다.
resume_existing_page이면 기존 ID를 조회해 부분 작성을 복구한다. 새 페이지 생성이나 무조건 append는 금지다.
inspect_before_retry이면 생성하지 않고 메인에 확인 필요 상태를 보고한다.
already_completed이면 저장된 URL을 반환한다.
notion-fetch로 생성 페이지를 읽고 제목·상위 페이지 ID·실제 Markdown 본문을 추출하여
record_publication(outcome=verified, observed_title=실제 제목,
observed_parent_page_id=실제 상위 페이지 ID, observed_markdown=실제 본문)에 전달한다.
재조회 값을 초안으로 대체하지 않는다. 상태가 completed일 때만 성공으로 보고한다.
승인 후 지식 수정, 다른 페이지 수정/이동/삭제, 내부 노션 지식 검색을 하지 않는다.
인증 오류·작성 결과 불명확·내용 불일치는 record_publication으로 기록하고 메인에 알린다.""",
}

PRESENTATION_COMMON = """get_document_blueprint(job_id, role)에서 작업에 고정된 형식을 먼저 확인한다.
legacy_v1 작업에는 새 형식의 필드를 요구하지 않는다. study_readable_v2에만 아래 지침을 적용한다.
"""
PRESENTATION = {
    "research": """도식의 경계·관계·순서·전제와 비교표에 필요한 원문 근거를 수집한다.
실제 화면은 공식 문서·프로젝트의 공개 자료만 사용하며 source ID, 원문 위치, 이미지 URL,
확인 시점·제품 버전·이용 조건을 반환한다. 직접 실습 캡처·사진 생성·외부 호스팅을 하지 않는다.""",
    "foundation": """설명에는 안정 ID와 key_point를 부여하고 핵심 문장→이유→예시 순으로 작성한다.
용어는 짧은 정의로 시작하고 필요성과 예시를 관련 본문에 배치해 중복을 줄인다.
대표 구조도·기초 비교표는 자신의 항목에 연결하고 표·그림의 claim_ids를 명시한다.
오해에는 related_item_id를 넣는다. 재정렬에도 항목 ID를 유지한다. 초기 미완성 초안 저장은 허용한다.
메인에 prepare_visual_assets를 요청하고 반환된 실제 그림·캡션·표를 원문과 교차 검토한다.
화살표·경계·생략과 의미상 중복을 확인하고 record_cross_review에 실제 읽은 presentation_hash를 전달한다.""",
    "advanced": """설명에는 안정 ID와 key_point를 부여하고 한 문단에 한 개념을 작성한다.
내부 실행 순서·타임라인·선택 기준은 자신의 항목에 연결한 도식·표로 설명한다.
표·그림은 claim_ids에 연결하고 전제와 확인된 사실을 해석과 구분한다. 재정렬에도 ID를 유지한다.
메인에 prepare_visual_assets를 요청하고 실제 렌더링 그림·캡션·표를 원문과 교차 검토한다.
화살표·경계·생략·질문 중복을 확인하고 실제 읽은 presentation_hash를 record_cross_review에 전달한다.""",
    "notion_writer": """현재 draft의 bundle_hash와 사용자 승인 bundle_hash가 일치하는지 확인한다.
발행 전에 실제 Notion 도구의 Mermaid·표·details 토글·이미지 지원을 확인한다. 확인되지 않은 지원을 추정하지 않는다.
prepare_publication의 manifest와 본문을 사용한다. required_uploads가 있으면 실제 도구의 업로드 지원과 안정 자산 참조를 확인한다.
승인된 자산만 업로드하고 실제 영수증을 record_publication_asset에 즉시 기록한다. 기존 영수증은 재사용한다.
최초 create_page를 받은 동일 실행에서는 업로드 후 get_publication_payload로 자리표시자가 치환된 본문을 조회한다.
이 조회는 새 생성 권한이 아니다. 재시작한 실행은 prepare_publication의 inspect_before_retry를 계속 따라야 한다.
지원이 없거나 업로드 결과가 불명확하면 멈추고 record_publication(uncertain)으로 기록한다. 외부 호스팅·다른 인증 경로를 추가하지 않는다.
asset:// 자리표시자가 남은 본문을 발행하지 않는다. 그림 대신 코드나 링크로 임의 대체하지 않는다.
재조회 본문의 표·토글 자식·도식·이미지·캡션을 생략하지 말고 observed_markdown으로 전달한다.
이미지의 실제 읽힌 안정 참조 또는 바이트 해시를 observed_assets에 기록한다. 기대 해시를 관찰값으로 복사하지 않는다.
실제 화면에서 그림·표·답안 접기가 보존된 경우에만 observed_visual_check=true로 기록한다.
화면 확인 도구가 없으면 완료를 주장하지 말고 제한을 보고한다. 표시 형식을 바꾸려면 새 초안과 승인이 필요하다.""",
}


def presentation_instructions(role: str) -> str:
    return (
        "[CS-STUDY PRESENTATION V2]\n"
        + PRESENTATION_COMMON
        + PRESENTATION[role]
        + "\n[/CS-STUDY PRESENTATION V2]"
    )


def upgrade_presentation_prompt(path: Path, role: str) -> None:
    text = path.read_text(encoding="utf-8-sig")
    current = tomllib.loads(text)["developer_instructions"]
    current = re.sub(
        r"\n?\[CS-STUDY PRESENTATION V2\].*?\[/CS-STUDY PRESENTATION V2\]", "", current, flags=re.S
    ).rstrip()
    updated = current + "\n\n" + presentation_instructions(role)
    # Replace just the TOML scalar, retaining all user model settings and comments.
    pattern = r'(?m)^(developer_instructions\s*=\s*)("""[\s\S]*?"""|\x27\x27\x27[\s\S]*?\x27\x27\x27|"(?:\\.|[^"\\])*"|\x27[^\x27]*\x27)'
    text, count = re.subn(pattern, lambda match: match[1] + quote(updated), text, count=1)
    if count != 1 or tomllib.loads(text)["developer_instructions"] != updated:
        raise ValueError(f"역할 지침을 안전하게 병합하지 못했습니다: {path}")
    path.write_text(text, encoding="utf-8")


def quote(value: str) -> str:
    # TOML basic strings share these JSON escapes; ensure Unicode stays legible.
    import json

    return json.dumps(value, ensure_ascii=False)


def managed_block(root: Path, role: str) -> str:
    python = Path(sys.executable).resolve().as_posix()
    args = ["-m", "cs_study_mcp", "--project", root.as_posix(), "serve", "--role", role]
    lines = [
        "# BEGIN CS-STUDY MANAGED CONNECTIONS",
        "[mcp_servers.cs_study]",
        f"command = {quote(python)}",
        f"args = [{', '.join(quote(arg) for arg in args)}]",
        f"cwd = {quote(root.as_posix())}",
        "enabled = true",
        "required = true",
        "startup_timeout_sec = 30",
        "tool_timeout_sec = 60",
        f"enabled_tools = [{', '.join(quote(tool) for tool in sorted(TOOLS_BY_ROLE[role]))}]",
        "[mcp_servers.cs_study.env]",
        'PYTHONUTF8 = "1"',
        "",
        "[mcp_servers.notion]",
        'url = "https://mcp.notion.com/mcp"',
        f"enabled = {'true' if role == 'notion_writer' else 'false'}",
        f"required = {'true' if role == 'notion_writer' else 'false'}",
        "startup_timeout_sec = 60",
        "tool_timeout_sec = 120",
    ]
    if role == "notion_writer":
        lines.extend(
            [
                'enabled_tools = ["notion-get-tool-access", "notion-fetch", "notion-create-pages", "notion-update-page"]',
            ]
        )
    lines.append("# END CS-STUDY MANAGED CONNECTIONS")
    return "\n".join(lines)


def write_config(path: Path, header: str, block: str) -> None:
    if path.exists():
        text = path.read_text(encoding="utf-8-sig")
        pattern = r"# BEGIN CS-STUDY MANAGED CONNECTIONS.*?# END CS-STUDY MANAGED CONNECTIONS"
        if not re.search(pattern, text, flags=re.S):
            raise ValueError(f"기존 구성을 덮어쓰지 않습니다. 수동으로 병합하세요: {path}")
        text = re.sub(pattern, lambda _: block, text, flags=re.S)
    else:
        text = header + "\n\n" + block + "\n"
    tomllib.loads(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def configure(root: Path, upgrade_presentation: bool = False) -> None:
    root = root.resolve()
    main = (
        "# 기본 모델은 Codex 설정을 상속합니다. 필요하면 model/model_reasoning_effort를 지정하세요.\n"
        'web_search = "disabled"\n\n'
        "[agents]\n"
        "enabled = true\n"
        "max_concurrent_threads_per_session = 3"
    )
    write_config(root / ".codex" / "config.toml", main, managed_block(root, "main"))
    for role, description in DESCRIPTIONS.items():
        header = (
            f"name = {quote(role)}\n"
            f"description = {quote(description)}\n"
            '# model = "계정에서 사용 가능한 모델 ID"\n'
            '# model_reasoning_effort = "high"\n'
            f"web_search = {quote('live' if role == 'research' else 'disabled')}\n"
            f"developer_instructions = {quote(INSTRUCTIONS[role] + chr(10) + chr(10) + presentation_instructions(role))}"
        )
        write_config(root / ".codex" / "agents" / f"{role}.toml", header, managed_block(root, role))
        if upgrade_presentation:
            upgrade_presentation_prompt(root / ".codex" / "agents" / f"{role}.toml", role)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--upgrade-presentation",
        action="store_true",
        help="기존 역할의 사용자 지침을 보존하며 v2 문서 지침만 병합",
    )
    args = parser.parse_args()
    configure(args.project, args.upgrade_presentation)
    print("Codex 프로젝트 구성 완료. 변경된 설정은 새 Codex 세션에서 확인하세요.")
