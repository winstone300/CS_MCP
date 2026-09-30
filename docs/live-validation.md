# 실제 계정 연동 검증

자동 테스트의 가상 근거와 실제 웹 원문 검증을 혼동하지 않는다.
실제 검증은 Codex의 모델 실행/역할 설정 로딩, 웹 검색, Notion OAuth가 준비된 뒤 수행한다.

## 사전 점검

1. setup.ps1, doctor, pytest를 통과한다.
2. 이 프로젝트를 신뢰하고 새 Codex 채팅에서 메인 cs_study를 확인한다.
3. run-role --check로 research의 웹 연결, 역할별 저장·교차 검토 도구의 노출 여부를 확인한다.
   진단에서 저장 도구를 호출하지 않는다. 일반 writer 진단도 Notion을 호출하지 않으며,
   별도 --check-presentation에서만 대상·규격의 읽기를 수행한다. Notion 쓰기는 승인된 일반 실행에서만 한다.
4. codex login status가 ChatGPT 로그인 상태인지 확인한다.
5. README의 Notion OAuth 명령을 완료하고 대상 페이지 읽기를 확인한다. 실제 생성은 시험 초안에 대한 사용자 승인 후 확인한다.
6. 기존에 설치한 다른 Notion 플러그인/별칭이 역할 제한을 우회하지 않는지 확인한다.

## 세 가지 검증 주제

| 주제 | 기초 확인 | 심화·실제 사례 확인 |
|---|---|---|
| 프로세스·스레드 | 주소 공간·실행 단위·동기화 용어 | 동시성·병렬성 전제, 공식 문서로 확인한 구현 사례 |
| TCP·UDP | 연결·전달·순서·신뢰성 | 지연·손실·응용 설계의 장단점, 실제 프로토콜 적용 |
| 데이터베이스 인덱스 | 인덱스·탐색·선택도 | 실행계획·쓰기 비용·인덱스 설계 사례 |

각 주제를 새 작업으로 시작하고 웹 원문을 실제 확인한다.
기초 3개·심화 3개 질문, 실제 사례 1개 이상, 모든 핵심 주장의 근거를 확인한다.
양쪽 교차검토를 마친 초안을 사용자에게 제시하고 명시적 발행 답변을 기다린다.
승인 후 지정 상위 페이지에 새 페이지 1개를 만들고 실제 fetch 결과로 완료를 기록한다.

## 기록할 결과

- 작업 ID, 실행 날짜, Codex 버전, 적용 모델/추론 설정
- 각 역할의 실제 실행 여부와 노출 도구
- 출처와 실제 사례 검증 결과
- 사용자에게 표시한 초안 버전/해시와 명시적 승인
- 생성 페이지 ID·URL, 실제 재조회 일치 여부
- 중단/인증 만료/재시도 시험 여부와 관찰 결과

검증 전에는 실제 실행이나 노션 발행이 성공했다고 표기하지 않는다.
로그와 이력에 OAuth 토큰이나 계정 비밀 값을 기록하지 않는다.

## 2026-09-29 구현 검증 결과

- Windows, Python 3.12.10, Codex 0.158.0-alpha.2.1에서 검사했다.
- 자동 테스트 46개 통과. 공식 MCP 클라이언트의 stdio 연결과 5개 역할별 도구 목록을 포함한다.
- Ruff, pip 의존성 검사, doctor 로컬 검사, wheel/sdist 빌드가 통과했다.
- ChatGPT 로그인 상태와 Notion OAuth 로그인 성공을 확인했다. 토큰은 Codex가 관리한다.
- Notion을 읽기 도구만 허용한 별도 연결 시험에서 notion-fetch를 실제 호출했다.
  최초 지정 상위 페이지는 404 object_not_found를 반환했다.
  이후 사용자가 지정한 제목 `CS 스터디`로 검색했다. 첫 검색의 504 오류 후 한 차례 재시도해
  정확히 일치하는 페이지 1개를 찾았고 notion-fetch 조회도 성공했다.
  확인한 페이지 ID는 `3cef6254-d56c-80b7-89cf-edb643163efd`이며 로컬 발행 대상을 갱신했다.
  조회 권한은 확인했지만 페이지 생성·쓰기 권한은 아직 시험하지 않았다.
- 새 CLI 세션의 기본 프로젝트 설정 로딩 시험에서는 cs_study와 custom 역할이 노출되지 않았다.
  프로젝트 신뢰 및 새 세션에서의 역할 로딩 확인이 남아 있다. doctor의 성공은 런타임 역할 발견을 뜻하지 않는다.
- 세 주제의 자동 테스트는 가상 근거를 사용한 모의 시험이다. 실제 웹 조사부터 사용자 승인·Notion 발행까지의
  세 주제 전체 시험은 미완료이며, 실제 문서를 생성하거나 발행하지 않았다.

## 2026-09-30 역할 실행 복구

사용자가 보고한 작업 `4fad3cbb-4709-44a7-b430-e1caa772c941`로 읽기 전용 재현을 수행했다.
내장 custom subagent에 agent_type을 지정해도 메인 도구가 상속되는 현상을 확인했다.
fork_turns=none과 명시적 config_file 지정에서도 동일했다. 프로젝트 신뢰는 활성화된 상태였다.

사용자의 명시적 선택에 따라 역할 TOML을 독립 Codex CLI의 구성 인자에 직접 적용하는
run-role 명령을 추가했다. ChatGPT 인증, 동일 SQLite DB, 역할별 도구 제한을 유지한다.
일반 에이전트로의 암묵적 대체나 메인 도구 범위 확대는 적용하지 않았다.

| 실제 CLI 진단 | 결과 |
|---|---|
| research | 실제 role=research, save_research 노출, get_study 성공, 내장 웹으로 Python 원문 열기 성공 |
| foundation | 실제 role=foundation, save_knowledge_section·record_cross_review 노출, get_study 성공 |
| advanced | 실제 role=advanced, save_knowledge_section·record_cross_review 노출, get_study 성공 |
| notion_writer (--check) | 실제 role=notion_writer, get_study 성공, Notion 읽기 도구 노출, 발행·쓰기 도구 제외 |

세 역할 모두 main의 create_study·save_draft·record_review와 Notion 쓰기 도구가 없었다.
진단 결과 JSON의 역할·도구 집합·웹 호출 결과를 검사했다. 위 쓰기 도구는 노출 여부만 확인했고,
실제 사용자 작업에 시험용 자료를 저장하지 않았다. 도구의 저장 동작은 임시 DB 자동 테스트로 검증한다.

자동 테스트 61개 통과. 역할별 CLI 인자, 전달 모델과 역할 모델의 우선순위, 승인 전 writer 차단,
최대 3개 실행 슬롯, 실제 역할 불일치의 진단 실패 처리와 기존 워크플로 회귀 테스트를 포함한다.
규칙 스냅샷·기존 작업 ID는 유지된다. 실제 조사·문서 작성·승인·발행은 사용자 작업 재개 시 수행한다.
writer의 Notion MCP는 required=true로 준비 완료를 기다리게 했다. writer 연결 진단도 통과했으며,
이 검사에서는 Notion 원문 조회·작성 호출을 하지 않았다. 실제 발행 성공을 뜻하지 않는다.

## 2026-09-30 문서 표현 v2 검증

이 절은 위의 기존 연결 시험과 구분되는 새 문서 형식의 결과다. **실제 Notion 문서 생성·변경·업로드,
새 본문의 재조회 일치, 도식·표·토글의 실제 화면 표시는 아직 시험하지 않았다.** 시험 초안과
발행 대상을 제시한 뒤 사용자의 명시적 승인을 받는 절차가 남아 있다. 개발 구현 요청을 발행 승인으로 기록하지 않았다.

### 로컬 렌더링과 기존 데이터 보존

`.cs-study/renderer-smoke-report.json`에서 구조도·타임라인·경쟁 상태 그림의 실제 PNG 생성 결과를 확인했다.
실행 환경은 Windows x64, Node v24.18.0, Chrome 154.0.8037.58이며 Mermaid CLI/Mermaid 12.0.0,
Puppeteer 25.12.0, Noto Sans KR 5.3.0을 사용했다. 기록된 파일 정보는 다음과 같다.

| 렌더링 입력 구분 | 형식·크기 | 자산 SHA-256 |
|---|---|---|
| structure | PNG, 12,943 bytes | `b457800fe7f07b579d6da49ce81d0cde114b87d7576b269058757b7a718f6a9d` |
| timeline | PNG, 31,056 bytes | `38193e1d9af4e74af1cd26b25ab51b223d137dafcb60631badba7bea130dbb26` |
| race | PNG, 29,171 bytes | `60de3ee9e9f70d139f462a5abf9f397cb08e8735715faaf83d659ff2665148d4` |

초기 보고서의 반복 렌더링 검사 `repeat_hash_equal`은 true였으나, 이후 동일한 기존 정의를 반복하는
추가 검사에서 GPU 래스터화의 ±1/255 색상 차이가 관찰됐다. 주소 공간 그림은 1,118,656픽셀 중
4,206픽셀, 경쟁 상태 그림은 2,450,364픽셀 중 7픽셀이 달랐다. 눈에 거의 드러나지 않는 차이도
승인 자산의 바이트 해시를 바꾸므로, 브라우저 인자에 `--disable-gpu`, `--force-color-profile=srgb`를 추가했다.
스크립트가 바뀌면 scriptHash와 renderer fingerprint도 자동으로 달라진다.

안정화 후 `.cs-study/renderer-repeat-final.json`에서 새 3종 정의를 각각 3회씩
(순차 2회와 병렬 1회) 렌더링한 SHA-256이 모두 일치함을 확인했다.
`same_environment_repeat_equal=true`이며, Windows x64·Node v24.18.0·Chrome 154.0.8037.92에서 수행했다.

| 최종 반복 검사 정의 | 3회 일치한 자산 SHA-256 |
|---|---|
| F-VIS-ADDRESS | `b8fe5e4a235377b714cbaeda6eb36e6988092afb163e96ad92f4d3a8a4acd373` |
| F-VIS-TIMELINE | `7d5be3d0bad5b95cb8d61ae2fda630d0d709b41e0f73e5402eb8eedf6642e59b` |
| A-VIS-LOST-UPDATE | `959d4566685f041def3ced893fc29c4d9b347832d94bd6a08e62992de80f8303` |

이는 해당 환경의 검사 결과이며, 다른 브라우저·운영체제에서도 같은 바이트가 나온다는 보장은 아니다.
검사 당시 검토 중이던 foundation v3·advanced v2의 자산은 불변으로 유지했고, 새 실험 출력으로 교체하지 않았다.
실제 자산 해시와 실행 환경 fingerprint를 승인 묶음에 연결한다. 로컬 그림 생성은 CS 주장에 대한
원문 검증이나 Notion 표시 검증을 대신하지 않는다.

기존 DB의 v1 백업은 `.cs-study/backups/schema-v1-20260930T021954965640/`에 있다.
변경 전 `.cs-study/backups/existing-records-before-presentation.json`과 현재 DB를
`.cs-study/verify_existing.py`로 읽기 전용 비교한 결과는 다음과 같다.

~~~text
All pre-existing rows, hashes, approvals and publication records preserved.
~~~

이 검사는 기존 jobs·sources·sections·drafts·reviews·publications·cross_reviews·events의
당시 컬럼과 행을 비교한다. 기존 본문·해시·승인·발행 이력이 보존됐고, 새 형식의 필드를 요구하도록
기존 작업을 자동 전환하지 않았다. Python 3.12.10은 허용된 실행 환경에서 정상 동작했다.
초기에 보인 기반 Python 프로세스 실행 실패는 샌드박스 권한과 구분해 확인했으며 재설치하지 않았다.

### writer의 실제 읽기 전용 규격 진단

작업 `5dc70aad-bba8-4fb3-a220-28282657cc0d`에 대해 독립 notion_writer CLI의
`--check-presentation` 진단을 실행했다. 실행 ID는 `dd0d64bc-457a-4979-b2c7-6edc22e0873b`다.
`.cs-study/runs/<run_id>/run.json`에는 `check_only=true`, `inspect_presentation=true`,
종료 코드 0, `diagnostic_passed=true`가 기록됐다. 근거 원문 발췌는 같은 디렉터리의 `result.md`에 있다.

| 확인 항목 | 실제 관찰 |
|---|---|
| 로컬 역할·도구 | role=notion_writer, 실제 로컬 바인딩은 get_runtime_info/get_study만 노출, get_study 성공 |
| Notion 접근 | notion-get-tool-access와 notion-fetch 읽기 바인딩 확인, 쓰기 바인딩 없음 |
| 대상 조회 | 설정된 상위 페이지 `CS 스터디` 읽기 성공. 읽은 페이지에는 새 형식의 그림·표·토글 예시가 없었음 |
| 원문 규격 | Notion MCP resource 목록에서 `notion://docs/enhanced-markdown-spec`을 발견하고 본문 조회 |
| Mermaid | 규격에 mermaid 코드 펜스 명시. 작성 후 표시·재조회 미실시 |
| 표 | table/tr/td 문법, header-row/header-column/fit-page-width의 생략 기본값 false 확인 |
| 토글 | details/summary 및 자식의 탭 들여쓰기 요구 확인. 실제 답안 접기·자식 보존 검증 미실시 |
| 이미지 | 이미지 Markdown, 업로드 응답 suggested_markdown, file-upload:// 참조 문법 확인. 실제 업로드·자산 식별 검증 미실시 |

규격에 맞춰 Notion용 표는 `<table header-row="true">`로 첫 행 제목을 보존하고, details 자식 줄에는
탭을 붙인다. 일반 Markdown용 파이프 표·별도 답안 영역은 유지한다. 원문의 details 예시 자체에는
들여쓰기가 없지만 설명의 “Use tabs for indentation” 및 자식 들여쓰기 요구를 함께 확인했다.

도구 정보 응답은 create_file_upload/create_attachment를 available로 표시했지만 현재 역할의 허용된
호출 바인딩에는 업로드 도구가 없다. 이 응답을 실제 사용 가능성이나 쓰기 성공으로 해석하지 않는다.
생성 도식은 우선 Mermaid 경로로 시험한다. 공개 스크린샷은 승인된 원본 URL로 표시할 수 있도록
구현했지만 실제 원격 바이트 해시 확인이 필요하며, URL 문자열만 일치하면 완료할 수 없다.

### 남은 수용 시험과 최종 집계

자동 테스트는 임시 DB와 합성 근거·모의 발행 응답으로 구조·권한·버전·승인·복구를 검증한다.
최종 전체 `.venv/Scripts/python.exe -m pytest` 실행은 **168개 통과, 26.54초**였고,
`.venv/Scripts/python.exe -m ruff check .`는 **All checks passed**였다.
이 결과는 로컬 자동 검증이며, 실제 Notion 쓰기·화면 표시·새 본문의 재조회 시험은 여전히 미실시다.

프로세스·스레드 시험 작업 `5dc70aad-bba8-4fb3-a220-28282657cc0d`는 실제 research 조사와
foundation v3·advanced v2 작성을 거쳐 양쪽 교차 검토를 통과했다(research_revision=1).
각 역할은 지정된 PNG 3개를 열고 저장된 원문 근거와 대조했다. blocking은 0건이며,
양쪽에 공통된 권고는 “두 레인”을 “두 실행 자원”으로 바꾸는 표현 개선 1건이다.
구조·표현 자동 검사에는 오류와 경고가 없다.

승인용 초안은 v2이며 본문 SHA-256은
`1d7b5145a4c2d3600e986b8f3cee2ac025ad04f91a1a9cd41a41b23648e69938`, bundle_hash는
`c5ccd3f4ba40e8ea973530ef7a1483a8b25e53c4ed4eb10f62fe65056de0eafb`다.
`.cs-study/drafts/<job_id>/v2.md`와 같은 이름의 HTML이 같은 저장 초안에서 생성됐다.
로컬 Chrome의 1280px·390px 화면에서 이미지 3개 로딩, 표 2개, 닫힌 답안 6개 및 실제 답안
펼치기를 확인했다. 문서 전체 가로 넘침은 없고, 그림은 원본 확대·가로 스크롤, 표는 최소 열 너비로
확인했다. 결과는 `.cs-study/previews/qa.json`과 PNG들에 있다. 이 검사는 실제 Notion 화면 시험이 아니다.

실제 역할 실행에서 보고서의 긴 대시 때문에 Windows cp949 출력이 실패한 사례도 발견했다.
저장된 교차 검토는 성공했지만 CLI 결과 출력만 실패한 것을 구분했으며, CLI stdout/stderr를 UTF-8로
설정하고 비ASCII 보고서 회귀 검사를 추가했다. 재실행으로 검토를 중복 생성하지 않았다.

현재 데스크톱 대화의 장기 실행 main MCP는 이전 도구 목록을 유지하고 있다. 새 CLI의 `doctor`는
모든 역할의 도구·서버 설정 일치를 확인했고 실제 작업은 최신 독립 CLI에서 수행했다. 데스크톱에서
새 구성표·표현 검증 도구를 사용하려면 cs_study 연결을 다시 시작해야 한다.

실제 수용 시험의 남은 발행 절차는 다음과 같다.

1. 전체 초안, 원문 근거와 교차 검토 결과, 실제 로컬 그림·HTML 미리보기, 대상 페이지,
   초안 버전·본문 hash·bundle_hash를 제시한다.
2. 그 초안을 발행하라는 실제 사용자 답변 뒤에만 승인을 기록하고 writer를 일반 실행한다.
3. 생성 응답의 page ID를 즉시 저장하고 전체 본문을 다시 조회한다. 블록 순서·표 셀·헤더·토글 답안·
   Mermaid 정의·캡션·이미지를 제거하지 않고 비교한다.
4. 실제 Notion 화면에서 도식 표시와 답안 접기, 데스크톱·좁은 화면의 가독성을 확인한다.
   화면 확인을 수행한 경우에만 observed_visual_check=true로 기록한다.
5. 재조회와 화면 검사, completed 기록 뒤에만 최종 URL과 실제 성공을 보고한다.

이 절에 실제 페이지 생성 ID·완료 기록이 추가되기 전까지 새 문서 형식의 Notion 수용 시험은 미완료다.
