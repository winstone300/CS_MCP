# CS Study MCP

Codex 메인이 검색·기초·심화·노션 작성 역할의 독립 Codex CLI를 조율하는 로컬 CS 학습 도구입니다.
LLM API 키 없이 ChatGPT 로그인 기반 Codex를 사용합니다. Python MCP는 AI 추론을 실행하지 않으며,
출처·문서 버전·교차 검토·승인·발행 이력을 관리합니다.

새 작업은 `study_topic_v3` 형식을 사용합니다. 기초·심화의 작성 책임을 유지하면서 주제별 중첩
토글에 본문을 배치하고, 전체 Markdown 미리보기를 양쪽 역할이 검토한 뒤 그 후보를 승인용 초안으로 확정합니다.
기존 `legacy_v1`과 `study_readable_v2` 작업은 본문·해시·승인·발행 복구 방식을 유지합니다.
도구 입력, 후보 재개와 검증 순서는 [주제별 문서 v3 운영 안내](docs/topic-v3.md)를 참고하세요.
**새 형식의 실제 Notion 전체 본문·자산 재조회 수용 시험은 아직 완료되지 않았습니다.**
로컬 구현과 자동 테스트 결과를 실제 Notion 발행 성공으로 해석하지 마세요.

## 빠른 시작 (Windows / PowerShell)

Python 3.12와 Codex가 필요합니다. 프로젝트 루트에서 실행합니다.

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
codex login
~~~

setup은 프로젝트 .venv에 고정된 의존성을 설치하고 프로젝트 전용 Codex 구성을 만듭니다.
전역 Codex 설정이나 로그인 토큰을 복사하지 않습니다. 기존 역할 프롬프트와 모델 설정은 보존하고,
표시된 관리 구간의 연결 경로와 도구 목록만 갱신합니다.

기존 역할의 사용자 지침과 모델 설정을 보존하면서 새 문서 지침을 명시적으로 병합하려면 실행합니다.

~~~powershell
& ./.venv/Scripts/python.exe scripts/configure_codex.py --upgrade-presentation
~~~

도식에는 Node.js 22.13 이상과 로컬 Chrome/Edge/Chromium도 필요합니다. 프로젝트 루트에서
`npm ci --ignore-scripts`로 잠긴 Mermaid·한글 폰트 의존성을 설치합니다. 설치 과정에서 브라우저를
자동 다운로드하지 않습니다. 브라우저 선택·렌더링·스크린샷 등록은 [문서 표현 운영 안내](docs/presentation.md)를 참고하세요.

Notion 대상 설정과 OAuth 연결:

~~~powershell
& ./.venv/Scripts/python.exe -m cs_study_mcp configure --notion-parent "https://app.notion.com/p/3cef6254d56c80b789cfedb643163efd"
codex -c 'mcp_servers.notion.url="https://mcp.notion.com/mcp"' mcp login notion
~~~

OAuth는 본인의 브라우저에서 완료해야 합니다. 기본 메인 역할에서는 Notion 도구를 비활성화하고
notion_writer 역할에서만 활성화합니다. 대상은 study.local.toml에 저장되며 Git에서 제외됩니다.
설정 변경 후 Codex에서 이 프로젝트를 신뢰하고 새 채팅을 엽니다.
프로젝트 구성이 로드되기 전에는 메인 MCP가 보이지 않을 수 있습니다. 새 채팅에서
cs_study의 get_runtime_info가 role=main을 반환하는지 먼저 확인하세요. Notion 조회가 404라면
연결한 계정·워크스페이스에서 대상 페이지에 접근할 수 있는지 확인해야 합니다.

~~~text
프로세스와 스레드 차이를 CS 스터디 문서로 만들어줘.
AGENTS.md의 run-role CLI 방식으로 research, foundation, advanced 역할을 실행하고 초안을 보여줘.
노션 발행은 내가 초안을 승인한 뒤 진행해.
~~~

사용자는 초안과 대상을 확인한 뒤 "이 초안을 해당 CS 페이지 아래에 발행해줘"라고 답합니다.
메인은 실제 사용자 메시지를 기록하고 writer를 실행합니다.

## 현재 역할 실행 방식과 기존 작업 재개

Codex 0.158.0-alpha.2.1의 현재 환경에서 내장 custom subagent에 역할 TOML을 지정해도
부모의 메인 MCP·웹 도구 구성이 상속되는 문제가 실제 재현됐습니다. 새 문맥 실행과 명시적
config_file 지정으로도 해소되지 않았습니다. 따라서 사용자가 승인한 호환 방식으로
역할 TOML을 독립 Codex CLI 세션의 설정 인자에 직접 적용합니다. 역할 이름 등록만으로는 연결 성공이 아닙니다.

메인이 사용하는 명령은 다음과 같습니다. 평소 사용자는 위의 학습 요청만 입력하면 됩니다.

~~~powershell
cd C:\Users\SSAFY\Desktop\CS_MCP
# 읽기 전용 연결 진단: 실제 서버 역할, 필수 도구, research의 웹 연결 확인
& .\.venv\Scripts\python.exe -m cs_study_mcp run-role research --job-id "<job_id>" --check
# 작업 파일에는 조사 범위·문서 버전·담당 산출물을 구체적으로 작성
& .\.venv\Scripts\python.exe -m cs_study_mcp run-role research --job-id "<job_id>" --task-file ".cs-study/tasks/research.txt"
~~~

foundation, advanced, notion_writer에도 같은 명령 형식을 사용합니다. writer의 일반 실행은
현재 초안 해시와 대상에 연결된 승인이 없으면 시작 전에 차단됩니다. writer의 `--check`에서는
발행 도구와 Notion 쓰기 도구를 제외합니다. 진단 명령도 ChatGPT 사용량을 소비합니다.
실행 슬롯은 프로젝트당 최대 3개이며, 모든 역할은 같은 SQLite 작업을 사용합니다.

일반 `--check`는 Notion을 호출하지 않습니다. writer의 `--check-presentation`은 같은 읽기 전용
도구 제한에서 설정된 상위 페이지, 실제 도구 정보, 제공된 Markdown 규격을 조회합니다.
이 옵션은 writer만 사용할 수 있으며 함께 전달된 작업 지시를 실행하지 않습니다.
표현 규격을 읽었다는 사실과 실제 페이지 생성·표시·재조회 검증은 구분합니다.

~~~powershell
& ./.venv/Scripts/python.exe -m cs_study_mcp run-role notion_writer --job-id "<job_id>" --check-presentation
~~~

실행 기록은 `.cs-study/runs/<run_id>/run.json`, 보고서는 `result.md`, 로그는 `execution.log`입니다.
finished는 Codex 프로세스 종료 상태입니다. 학습 완료·검증 통과·발행 여부는 DB와 보고서를 별도로 확인합니다.
실행 창을 강제 종료했다면 남아 있는 Codex 프로세스의 종료를 확인하고 재개하세요.

이번에 중단된 작업은 새 작업을 만들지 않고, 새 메인 채팅에 아래처럼 요청합니다.

~~~text
AGENTS.md를 다시 읽고, 승인한 독립 Codex CLI(run-role) 방식으로
작업 4fad3cbb-4709-44a7-b430-e1caa772c941를 재개해줘.
실제 역할과 도구 연결을 확인하고 조사부터 이어서 통합 초안을 보여줘.
기존 규칙 스냅샷과 작업 ID를 유지하고, 노션 발행은 초안 승인 후 진행해.
~~~

## 구조

| 구성 | 책임 |
|---|---|
| AGENTS.md | 메인 흐름, 공통 품질 기준, 승인과 복구 규칙 |
| .codex/agents/research.toml | 독립 CLI에 직접 적용하는 웹 원문·근거 조사 설정 |
| .codex/agents/foundation.toml | 용어·기본 원리·기초 질문, 심화 교차 검토 |
| .codex/agents/advanced.toml | 심화 동작·트레이드오프·실제 사례, 기초 교차 검토 |
| .codex/agents/notion_writer.toml | 승인된 초안 발행·재조회 |
| src/cs_study_mcp | Pydantic 모델, SQLite 트랜잭션, 검증·렌더링, 역할별 MCP, CLI 실행기 |
| .cs-study/ | 로컬 DB, 불변 이미지 자산, Markdown 미리보기, 마이그레이션 백업 (Git 제외) |

메인은 Codex가 실행합니다. MCP 서버가 메인을 실행하거나 Codex 인증 토큰으로 모델 API를 직접 호출하지 않습니다.
자료 수집은 Codex 내장 웹 검색이며, 별도 검색 API·임베딩·내부 노션/파일 검색은 없습니다.
로컬 실행은 오프라인 LLM을 뜻하지 않습니다. Codex 모델·웹 검색·Notion에는 인터넷이 필요하고 계정의 사용량 제한이 적용됩니다.

## 모델과 도구 설정

기본 모델은 실행한 Codex CLI의 설정을 사용합니다. 메인에서 별도 선택한 모델·추론 강도는
run-role의 `--model`·`--reasoning-effort`로 전달합니다. 역할별 TOML의 주석을 해제하고 사용 가능한 모델 ID와
지원되는 추론 강도를 지정할 수 있습니다.

~~~toml
# 예: .codex/agents/advanced.toml의 최상위 설정
model = "gpt-6-sol"
model_reasoning_effort = "high"
~~~

위 모델명은 예시입니다. 역할 파일의 명시 설정은 전달된 메인 설정과 CLI 기본값보다 우선합니다.
같은 역할을 서로 다른 모델로 실험하려면 해당 역할 설정을 변경하고 새 세션으로 비교합니다.
Python MCP나 Notion MCP 자체에는 이 모델 설정이 전달되지 않습니다.

로컬 MCP는 역할별로 별도 프로세스를 실행하고 하나의 SQLite 저장소를 공유합니다.
서버의 실제 tools/list도 역할별로 다르며, foundation은 advanced 부분을 저장할 수 없습니다.
Notion 도구 제한은 Codex 구성의 허용 목록으로 적용합니다. 다른 전역 MCP 별칭이나 Notion 플러그인을
추가한 환경에서는 실제 에이전트 도구 목록을 확인하여 우회 경로가 없는지 확인해야 합니다.
이 구성은 단일 운영자의 워크플로 경계이며 악의적인 로컬 사용자에 대한 보안 격리가 아닙니다.

## 처리 흐름과 버전 규칙

1. 메인이 create_study로 작업을 만들고 규칙 원문/해시와 문서 프로필을 고정합니다. 각 역할은 get_document_blueprint로 해당 작업의 구성표를 읽습니다.
2. research가 실제 웹 원문을 읽고 save_research로 발췌·위치를 저장합니다.
3. foundation이 초기 기초 부분을 저장하면 advanced가 그 버전을 참조해 작성합니다.
4. 두 역할이 자기 부분을 완성하면 메인이 prepare_visual_assets로 검토용 그림을 준비합니다. v3에서는 제목·요약·참조 목차로 prepare_document_preview를 호출한 뒤 양쪽이 같은 버전의 전체 Markdown과 원문을 교차 검토하고 실제 읽은 presentation_hash를 기록합니다. 기존 v2는 저장된 절차를 따릅니다.
5. 메인이 validate_knowledge와 validate_presentation의 오류를 해결하고 save_draft로 검토한 후보의 preview_version/presentation_hash를 전달해 승인용 초안으로 확정합니다. v1/v2는 기존 DraftInput을 사용합니다. 권장 분량 경고는 오류와 구분합니다.
6. 전체 초안·그림·검증 결과·발행 대상·버전·hash·bundle_hash를 보여줍니다. 명시적 사용자 답변을 받은 뒤 record_review에 실제 메시지와 그 값을 기록합니다.
7. writer가 prepare_publication으로 승인된 본문을 받아 Notion MCP로 발행하고 재조회합니다.

내용이 바뀐 부분 문서는 새 버전으로 저장하며, 무수정 저장은 기존 버전을 유지합니다. expected_version은 동시 수정 충돌을 방지합니다.
기초 버전 변경 후에는 advanced의 참조 버전도 갱신해야 합니다. 새 출처가 추가되거나 양쪽 문서가
수정되면 현재 통합 초안을 무효화합니다. 교차 검토는 양쪽 버전과 연구 자료 리비전에 연결됩니다.
상태·이력·이전 초안은 DB에 남습니다.
record_cross_review에는 검토 당시 읽은 research_revision을 지정합니다. 최신 자료를 다시 확인하지 않고 번호만 갱신하면 안 됩니다.
v2의 presentation_hash는 양쪽 부분 문서와 준비한 실제 자산을 함께 고정합니다.
v3는 제목·요약·주제별 목차·조사 자료·전체 렌더링 출력과 후보 버전까지 고정합니다.
승인에는 Markdown 해시와 별도로 본문·배치·표·도식 정의·자산·출력 형식을 묶은 bundle_hash를 사용합니다.
관련 항목을 바꾸면 최신 교차 검토·초안·승인을 다시 받아야 합니다.

save_draft는 내용 검증과 양쪽 교차 검토를 통과한 최신 버전만 통합합니다.
기초와 심화는 각자의 주장 목록을 갖고, 설명/질문/사례는 자기 claim ID를 참조합니다.
주장은 작업에 등록된 source ID를 참조합니다. 원문이 바뀌면 source ID를 재사용하지 마세요.
get_question_blueprint의 question_count는 역할별 개수가 아니라 작업의 전체 질문 수입니다.

도구 스키마는 MCP tools/list 또는 [모델 정의](src/cs_study_mcp/models.py)에서 확인할 수 있습니다.
기초/심화의 빈 목록은 작업 중 초안 저장에는 허용되지만 필수 내용이 없으면 통합이 차단됩니다.
실제 사실 검증·의미상 중복 검출·사례 진위 판단은 Codex와 사용자 책임이며,
Python 검증이 사실의 진실성을 증명하는 것으로 해석하면 안 됩니다.

## 승인·발행·복구

사용자 승인 기록은 본인의 실제 대화상 결정을 기록합니다. record_review에 텍스트를 전달했다는
사실만으로 사람의 의사를 인증할 수는 없습니다. AGENTS.md의 승인 절차와 Codex의 실행 권한을 함께 적용합니다.
Notion은 writer가 직접 호출하므로 이 Python 서버는 모든 외부 쓰기를 중계하는 보안 프록시가 아닙니다.

| prepare_publication action | writer 동작 |
|---|---|
| create_page | 이번 시도에서만 새 페이지 생성. 반환 직후 page ID를 기록 |
| resume_existing_page | 기존 ID를 조회한 뒤 해당 페이지의 부분 작성만 복구 |
| inspect_before_retry | 이전 생성 결과가 불명확하므로 생성 금지·사용자 확인 |
| already_completed | 저장된 URL 반환. 재작성하지 않음 |

Notion OAuth 접근과 대상 읽기 가능 여부를 확인한 다음 발행을 예약하세요.
예약 직후 프로세스가 종료되어 ID가 없으면 실제로 생성되지 않았더라도 보수적으로 확인 필요 상태가 됩니다.
ID가 확인되면 같은 attempt_id로 page_created를 기록해 기존 페이지로 복구할 수 있습니다.
생성 여부가 불명확한 동안 새 작업을 생성해 발행을 우회하지 마세요.
이 버전은 ID 없는 불명확한 생성에 대한 자동 재시도를 제공하지 않습니다.

verified에는 notion-fetch가 반환한 실제 제목·상위 페이지 ID·본문 Markdown을
observed_title·observed_parent_page_id·observed_markdown으로 전달합니다.
응답의 메타데이터 래퍼를 제거하고 본문만 추출하세요. 승인 본문을 복사하여 제출하지 마세요.
기존 형식은 줄바꿈·빈 블록 등 제한된 표현 차이만 정규화하고 본문·제목·상위 페이지를 비교합니다.
새 형식은 블록 순서, 표의 셀, 토글 내부 답안, Mermaid 정의, 이미지와 캡션을 보존해 비교합니다.
이미지는 실제 자산 해시 또는 기록된 업로드 영수증과 재조회한 안정 참조를 대조합니다.
Notion 브라우저 화면 검증은 요구하지 않습니다. 실제 재조회한 제목·상위 페이지·본문 구조·자산이 일치하면 완료 처리합니다.
업로드·재조회 증거가 부족하거나 내용이 다르면 완료 처리하지 않습니다.
Notion이 다른 표현으로 변환하면 보수적으로 needs_attention이 될 수 있습니다.
코드의 들여쓰기와 내용은 유지하며, 내용 불일치를 무시하고 성공 처리하지 않습니다.
이미 발행이 시작된 작업은 이후 기본 대상 설정을 바꿔도 발행 이력에 고정된 대상으로 복구합니다.

재시작 후 작업 찾기:

~~~powershell
& ./.venv/Scripts/python.exe -m cs_study_mcp list
& ./.venv/Scripts/python.exe -m cs_study_mcp show "<job_id>"
& ./.venv/Scripts/python.exe -m cs_study_mcp export "<job_id>"
~~~

Codex에서 "작업 <job_id>를 저장된 규칙과 산출물로 재개해줘"라고 요청하세요.
Markdown 파일은 검토용 내보내기입니다. 파일 직접 수정은 DB/승인에 반영되지 않으며,
수정 의견을 담당 에이전트에 전달해 새 버전을 만들어야 합니다.
검토 파일은 Markdown으로만 내보냅니다. 기존 HTML 데이터·규칙 스냅샷·승인 해시는 보존하며, 기존 작업에도 화면 확인을 요구하지 않습니다.
내보내기 실패 시 초안 저장 결과의 export_error를 확인하고
`export`만 다시 실행하세요. 같은 초안을 저장하려고 save_draft를 반복하지 않습니다.
PC가 꺼졌거나 Codex가 종료된 동안 에이전트는 실행되지 않습니다.

v1/v2 DB를 처음 열 때 일관된 SQLite 백업을 `.cs-study/backups/schema-v<이전 버전>-*/`에 저장하고
schema v3로 마이그레이션합니다. 기존 문서 프로필과 행은 보존합니다. 기존 자산 디렉터리가 있으면 함께 백업합니다. 정기 백업·프로젝트 이동에도 DB와
`.cs-study/assets/`를 함께 보존하세요. 열린 SQLite 파일만 복사하는 방법은 사용하지 않습니다.

## 검사와 테스트

~~~powershell
& ./.venv/Scripts/python.exe -m cs_study_mcp doctor
& ./.venv/Scripts/python.exe -m pytest
& ./.venv/Scripts/python.exe -m ruff check .
~~~

doctor는 로컬 경로·역할별 허용 도구·서버 역할 인자를 검사하며 OAuth/웹 검색 성공을 보장하지 않습니다.
실제 역할 연결은 run-role의 --check로 확인합니다.
자동 테스트는 임시 SQLite와 공식 MCP 클라이언트를 사용하며 실제 Notion에 쓰지 않습니다.
세 가지 대표 주제에 대한 모의 워크플로와 실제 서비스 연결 시험은 구분합니다.
실계정 검증 절차와 기록은 [연동 검증 안내](docs/live-validation.md)를 참고하세요.

문제 해결:

- py -3.12 실행이 불가능하면 Python 3.12 설치 또는 앱 실행 별칭을 확인하세요.
- Codex 샌드박스에서만 기반 Python 프로세스를 만들 수 없으면 권한 제한과 직접 실행 결과를 먼저 구분하세요. 이 개선 작업에서 기존 Python 3.12.10은 허용된 실행 환경에서 정상 동작했으며 재설치하지 않았습니다.
- MCP가 import 오류로 시작하지 않으면 setup.ps1을 다시 실행하고 doctor를 확인하세요.
- 프로젝트를 이동했다면 setup.ps1 -SkipInstall로 절대 경로를 재생성하세요.
- 역할에 메인 도구만 보이면 내장 spawn_agent 사용 여부를 확인하고 run-role 방식으로 실행하세요.
- Git은 규칙/코드 관리에 사용합니다. DB·내보낸 문서·개인 설정·인증 정보는 커밋하지 않습니다.

## 공식 참고 문서

- [Codex 인증](https://learn.chatgpt.com/docs/auth)
- [Codex 서브에이전트](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Codex MCP 구성](https://learn.chatgpt.com/docs/extend/mcp)
- [공식 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Notion MCP 연결](https://developers.notion.com/guides/mcp/get-started-with-mcp)
- [Notion MCP 도구](https://developers.notion.com/guides/mcp/mcp-supported-tools)
