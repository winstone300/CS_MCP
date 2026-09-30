# 읽기 쉬운 학습 문서와 시각 자료 운영

현재 새 작업의 기본값은 `study_topic_v3`다. 아래 v2의 자료·자산·발행 규칙은 계속 유지하며,
주제별 목차와 교차 검토 전 전체 미리보기 절차는 [v3 운영 안내](topic-v3.md)를 따른다.
이 문서의 v2 작업 설명은 기존 작업의 보존된 동작을 설명한다.

`study_readable_v2`는 문서 순서와 근거 연결을 개선하고 로컬 그림·미리보기·승인 묶음을 제공합니다.
Python은 형식과 참조, 실제 자산 바이트, 재조회한 문서 구조를 검사합니다. 내용의 정확성, 그림의
화살표·경계·생략, 실제 사례 해석과 질문 중복은 Codex가 원문을 읽고 검토합니다.

현재 구현과 자동 테스트는 실제 Notion 그림·표·토글 표시 성공을 뜻하지 않습니다. 새 형식의 실제
Notion 수용 시험에는 별도 시험 초안 제시와 사용자의 명시적 발행 승인이 필요합니다.
개발 구현 요청을 학습 문서 발행 승인으로 사용하지 않습니다.

## 설치와 로컬 렌더러

Python 3.12 환경과 프로젝트 의존성 설치는 README의 `scripts/setup.ps1`을 따릅니다.
도식 렌더링에는 Node.js 22.13.0 이상과 설치된 Chrome·Edge·Chromium이 필요합니다.

~~~powershell
# 프로젝트 루트에서 실행합니다. package-lock.json의 고정 버전을 설치합니다.
npm ci --ignore-scripts
# 자동 검색으로 브라우저를 찾지 못할 때 실제 설치 경로를 지정합니다.
$env:CS_STUDY_CHROMIUM_PATH = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
# 기존 역할 프롬프트의 사용자 지침·모델 설정을 보존하며 표현 지침만 병합합니다.
& ./.venv/Scripts/python.exe scripts/configure_codex.py --upgrade-presentation
~~~

`--ignore-scripts`는 설치 스크립트에 의한 브라우저 다운로드를 생략합니다. 일반 `configure_codex.py`
실행은 기존 역할 프롬프트를 유지하며 연결·허용 도구만 갱신합니다. 명시적인
`--upgrade-presentation`은 `[CS-STUDY PRESENTATION V2]` 관리 구간만 교체·추가하며 여러 번 실행해도
중복 삽입하지 않습니다. 변경된 역할 설정은 새 Codex 세션에서 확인합니다.

현재 고정 패키지는 Mermaid CLI 12.0.0, Puppeteer 25.12.0, Noto Sans KR 5.3.0입니다.
렌더러는 새 headless 브라우저를 사용하고 외부 네트워크 요청을 차단합니다. 한글 폰트는 설치한
패키지에서 로드하며 도식의 외부 URL, HTML, 클릭 액션, 내장 설정 지시문을 허용하지 않습니다.
입력 정의는 100,000 UTF-8 바이트 이하이며 구조·흐름·시간 순서 도식을 PNG로 출력합니다.

같은 정의의 렌더링을 가능한 한 일관되게 만들지만 운영체제·브라우저 변경에 관계없는 바이트 동일성을
보장하지 않습니다. 실제 바이트 해시와 Node·브라우저·폰트·잠금 파일·스크립트의 실행 정보를 기록합니다.
기존 부분 문서 버전의 자산이 다른 바이트로 바뀌면 재승인 없이 교체하지 않습니다.
모델·이미지 생성 API, 외부 이미지 호스팅, 별도 Notion 인증 경로는 사용하지 않습니다.

## 새 작업의 작성·검토 순서

1. 메인이 `create_study`를 호출합니다. 기본 `presentation_profile`은 `study_readable_v2`,
   `visual_transport`는 `mermaid`입니다. 프로필 내용·버전·렌더러·검증 방식은 작업에 고정됩니다.
   이미지 전송을 사용할 경우 작업 생성 시 `visual_transport="image"`를 지정합니다.
2. 각 역할은 `get_study`의 규칙 스냅샷과 `get_document_blueprint(job_id, role)`를 읽습니다.
   메인이 역할 실행을 조율하고 research가 관계·순서·조건의 원문 근거를 수집합니다.
3. foundation이 기초 초안을 먼저 저장합니다. advanced는 해당 기초 버전을 읽고 작성합니다.
   본문 항목에는 안정적인 `id`, 설명에는 `key_point`, 오해에는 `related_item_id`를 제공합니다.
   필수 항목이 미완성인 초기 부분 문서는 저장할 수 있습니다.
4. 메인이 `prepare_visual_assets(job_id, expected_versions={"foundation": F, "advanced": A})`를 호출합니다.
   입력은 실제 읽은 버전이며, 아직 없는 부분은 0입니다. 렌더링 중 버전이 바뀌면 저장을 거절합니다.
   결과의 자산 파일·오류·경고·`presentation_hash`를 확인합니다. 이 도구는 Notion에 업로드하지 않습니다.
5. 양쪽 역할은 상대 문서, 원문, 실제 렌더링 그림을 읽고 `record_cross_review`를 남깁니다.
   읽은 양쪽 버전·`research_revision`·`presentation_hash`를 함께 전달합니다. 상대 문서는 직접 수정하지 않습니다.
6. 원작성자가 수정했다면 자산 준비와 양쪽 교차 검토를 반복합니다. 메인은 `validate_knowledge`와
   `validate_presentation`을 확인합니다. 없는 주장·잘못된 배치·누락되거나 변조된 자산은 오류입니다.
   긴 문단·큰 표·과도한 상단 그림은 경고이며, 의미 정확성 검토는 별도 항목입니다.
7. 메인이 검증된 주장에 연결한 요약과 최신 버전으로 `save_draft`를 호출합니다. 저장된 같은 묶음에서
   `.cs-study/drafts/<job_id>/v<version>.md`와 `.html` 미리보기를 생성합니다.
8. 메인은 전체 초안과 그림·검증 결과·발행 대상·초안 버전·`hash`·`bundle_hash`를 사용자에게 보여줍니다.
   그 초안을 발행하라는 실제 사용자 답변 뒤에만 `record_review(approve)`를 호출합니다.

`save_draft`는 권장 분량 경고와 통합을 막는 오류를 구분합니다. 문장을 임의로 요약하거나 중복 설명을
삭제하지 않습니다. 용어의 기존 필요성·예시도 작성자가 고치기 전까지 보존합니다.

## 배치·표·그림·스크린샷

문서 순서는 핵심 요약 → 대표 그림 → 목표·선수지식·짧은 용어 → 기초 → 심화 → 사례 → 면접 → 참고자료입니다.
첫 화면에는 대표 그림 하나를 권장하고 나머지는 설명 옆에 둡니다. 그림이 불필요한 주제에 수량을 강제하지 않습니다.

`Placement`는 `section="foundation" | "advanced"`와 `after_id`로 구성합니다.
`after_id=null`은 상단 overview이며, 그 외에는 작성자 자신의 본문 항목 ID를 참조합니다.
재정렬할 때 ID는 바꾸지 않습니다. 같은 위치에서는 비교표 배열 순서, 이어서 그림 배열 순서로 표시합니다.
기초 오해는 같은 역할의 용어·원리·예시에 연결하며 그 설명 바로 뒤에 표시합니다.

`ComparisonTable`은 `id`, `title`, `columns`, `rows`, `claim_ids`, `placement`를 가집니다.
행은 `cells`와 선택적인 `claim_ids`를 가지며 셀 수는 열 수와 같아야 합니다.
표 전체 근거가 없으면 각 행에 근거가 필요합니다. `|`와 셀 줄바꿈은 렌더러가 처리합니다.

`Visual`은 `id`, `kind`, `title`, `placement`, `caption`, `alt_text`, `claim_ids`를 공통으로 가집니다.
도식의 `kind`는 `structure`, `flow`, `timeline` 중 하나이고 `diagram_spec`에 Mermaid 원문을 저장합니다.
원문으로 검증할 전제·생략 범위를 캡션에 적고, 대체 텍스트에도 관계나 순서를 설명합니다.

스크린샷은 공식 문서·프로젝트에서 공개한 이미지를 출처와 이용 조건 확인 후 사용합니다.
직접 실습 환경을 실행해 화면을 캡처하는 기능은 제공하지 않습니다.

1. 확인한 PNG/JPEG 파일을 프로젝트 내부의 작업용 위치에 준비합니다. 등록 도구는 인터넷에서 다운로드하지 않습니다.
2. 메인이 `register_visual_asset(job_id, path)`로 등록합니다. 상대 경로는 프로젝트 기준입니다.
   외부 경로·20 MiB 초과 파일·스크린샷 SVG는 거절합니다.
3. 반환된 `hash`를 `Visual(kind="screenshot").asset_hash`에 연결합니다.
   `screenshot`에는 등록된 `source_id`, 원본 `image_url`, 원문 `locator`, `accessed_at`,
   확인할 수 있는 `product_version`, `usage_note`를 기록하고 `diagram_spec`은 제외합니다.
4. 담당 역할이 부분 문서를 저장한 후 메인이 `prepare_visual_assets`를 호출해 해당 버전에 연결합니다.

Notion용 스크린샷은 승인된 원본 공개 `image_url`을 이미지 문법에 사용합니다. 새로운 호스팅이나
업로드를 만들지 않습니다. 일반 Markdown의 자산 참조와 HTML 미리보기의 이미지는 등록한 로컬
불변 파일을 사용하며, 공개 URL도 승인 묶음에 포함됩니다. 원격 원본이 나중에 바뀔 수 있으므로
재조회 검증에는 실제 원격 이미지 바이트에서 확인한 해시가 필요합니다. URL 문자열 일치나
승인 해시를 복사한 값만으로는 완료 처리할 수 없습니다.

자산은 `.cs-study/assets/<sha256>.<확장자>`에 불변 저장합니다. DB가 작업·부분 문서 버전·그림 ID와
자산의 연결을 관리합니다. 누락·변조 검사는 등록 때만 수행하는 것이 아니라 통합·승인·발행 전에 반복합니다.

## 승인·Notion 재조회·복구

`hash`는 기존과 같이 Markdown 해시입니다. `presentation_hash`는 검토할 부분 문서·프로필·자산 묶음,
`bundle_hash`는 최종 본문·표·배치·그림·자산·출력 형식·미리보기를 묶은 승인 해시입니다.
시각 자료나 배치를 바꾼 뒤 오래된 해시를 재사용하지 않습니다. 파일 직접 편집은 DB 원본이나 승인 대상을 바꾸지 않습니다.

로컬 HTML에는 실제 이미지·표와 접힌 답안이 표시됩니다. 일반 Markdown은 질문을 모아 먼저 보여주고
답안을 별도 영역에 둡니다. Notion용 본문은 Mermaid 코드 펜스, `<table header-row="true">` 안의
`tr/td` 셀, 자식 줄을 탭으로 들여쓴 `details/summary` 표현을 사용합니다.
이는 발행할 표현 계약이며, 현재 연결에서 실제 렌더링이 검증되었다는 뜻은 아닙니다.

승인된 일반 실행의 notion_writer만 Notion 쓰기 도구를 사용합니다. `run-role ... --check`는 연결 진단이며
학습 자료·자산·교차 검토·승인·페이지를 저장하지 않고 Notion을 호출하지 않습니다.
writer 진단에서는 로컬 조회와 Notion 읽기 도구만 노출합니다.

~~~powershell
& ./.venv/Scripts/python.exe -m cs_study_mcp run-role notion_writer --job-id "<job_id>" --check-presentation
~~~

`--check-presentation`은 writer 전용 읽기 진단입니다. `--check` 동작을 강제하고 함께 전달한 일반 작업
지시는 실행하지 않습니다. `notion-get-tool-access`로 접근·도구 정보를, `notion-fetch`로 설정된 상위
페이지를 읽으며 Notion MCP가 제공하는 Markdown 규격 resource도 조회할 수 있습니다.
생성·변경·업로드는 금지하며 로컬 학습 산출물·승인·발행 상태를 바꾸지 않습니다.
실행 보고서의 `presentation_support`에는 대상 조회, Mermaid·표·토글·이미지 업로드의 규격 근거와
한계를 기록합니다. 불명확한 기능은 unknown이며, 읽기 진단 통과는 실제 쓰기·화면 표시 수용 시험 통과가 아닙니다.

2026-09-30 writer의 읽기 전용 진단에서는 Notion MCP가 제공한 `notion://docs/enhanced-markdown-spec`에서
위 Mermaid·표·토글 문법을 확인했습니다. 표의 `header-row`, `header-column`, `fit-page-width`는
생략 시 false이며, 문서의 비교표는 첫 행 제목을 보존하도록 `header-row="true"`를 명시합니다.
토글은 규격의 “Use tabs for indentation” 지시에 따라 자식 줄을 탭으로 들여씁니다.
설정된 상위 페이지 읽기도 성공했지만 새 본문 작성·실제 표시·재조회 일치는 아직 시험하지 않았습니다.
도구 정보 응답에는 업로드 기능이 표시됐으나 현재 역할의 허용된 호출 바인딩에는 업로드 도구가 없습니다.
따라서 현재 연결의 생성 도식 이미지 업로드·안정 참조 확인은 미검증이며, 우선 Mermaid 경로로 실제 수용 시험을 진행합니다.
공개 스크린샷은 원본 URL 표시 경로를 사용하되 실제 원격 바이트 확인을 할 수 없으면 완료를 차단합니다.

writer는 실제 도구의 Mermaid·표·토글 지원을 확인합니다. Mermaid 표시가 불가능하면 이미지 업로드와 안정적인
자산 식별을 지원하는지 확인합니다. 지원 경로가 없으면 제한을 보고하며 코드·링크·외부 호스팅으로 임의 대체하지 않습니다.
전송 형식은 작업에 고정되므로 다른 형식이 필요하면 승인한 내용을 몰래 바꾸지 않고 새 작업·초안·승인 절차를 따릅니다.

`prepare_publication`이 요구하는 업로드가 있으면 실제 도구로 업로드하고 응답의 `remote_ref`, `remote_url`과
승인 자산의 `asset_hash`를 `record_publication_asset`에 즉시 기록합니다. 기존 영수증은 재사용하고,
결과가 불명확하면 재업로드하지 않습니다. `asset://<hash>`는 내부 자리표시자이며 그대로 Notion에 발행하면 안 됩니다.
`remote_url`에는 실제 응답의 안전한 HTTPS 또는 `file-upload://` 참조를 사용할 수 있습니다.
인증 정보·공백·문법 제어 문자가 섞인 참조는 거절하며, 허용 스킴이라는 사실만으로 실제 업로드나
자산 동일성이 확인되지는 않습니다. 응답의 참조를 만들어내거나 승인 해시를 관찰 해시로 복사하지 않습니다.
업로드 영수증을 저장한 뒤에는 `get_publication_payload(job_id, attempt_id)`로 치환된 본문을 읽고
`required_uploads`가 비어 있는지 확인합니다. 이 읽기 도구는 발행을 재예약하거나 새 페이지 생성 권한을 주지 않습니다.

페이지 생성은 `create_page`를 받은 최초 시도에서만 한 번 수행하고 반환된 page ID를 즉시 기록합니다.
재개는 저장된 page ID·업로드 영수증·발행 당시 대상에 연결합니다. `inspect_before_retry`에서는
생성 결과 확인이 필요하며 새 생성이나 무조건 append로 우회하지 않습니다.

재조회 검증에는 실제 제목·상위 페이지 ID·전체 본문 Markdown을 전달합니다. 선택적으로 실제 본문의 canonical
블록을 `observed_blocks`에 함께 전달할 수 있으며 본문에서 파싱한 결과와도 일치해야 합니다.
이미지·표·토글을 삭제하거나 내부 답안을 평탄화하여 일치시키지 않습니다.
원격 이미지는 `observed_assets`의 실제 바이트 해시 또는 실제 안정 참조와 저장된 영수증 연결로 확인합니다.
서명 URL 문자열 일치만으로 동일성을 판정하지 않습니다.

writer는 실제 화면에서 도식·표·접힌 답안을 확인한 경우에만 `observed_visual_check=true`로 기록합니다.
재조회나 화면 확인에 필요한 도구가 없으면 완료를 주장하지 않습니다. 기대 본문·해시를 관찰 결과인 것처럼 복사하지 않습니다.

## 기존 작업·백업·수용 시험

기존 작업은 `legacy_v1`로 읽으며 새 필수 ID·그림·승인 묶음을 요구하지 않습니다. 기존 렌더러와 Markdown
검증, 초안 해시·승인·발행 이력을 유지합니다. 새 필드의 기본값 때문에 무수정 저장 버전이 올라가지 않습니다.

처음 v1 DB를 열 때 SQLite backup API로 `.cs-study/backups/schema-v1-<UTC시각>/study.sqlite3`를 저장하고
기존 `assets/`도 함께 복사한 뒤 트랜잭션으로 v2 스키마를 적용합니다. 실패하면 마이그레이션은 롤백합니다.
운영 중 백업도 DB와 불변 자산을 함께 보존해야 합니다. 실행 중인 `.sqlite3` 파일만 복사하면 WAL 내용이 빠질 수 있습니다.

초안 저장 뒤 내보내기만 실패하면 `export_error`가 반환됩니다. 초안은 DB에 있으므로 `save_draft`를 반복하지 않고
CLI `export <job_id>`로 같은 버전의 Markdown·HTML을 다시 내보냅니다.

~~~powershell
& ./.venv/Scripts/python.exe -m pytest
& ./.venv/Scripts/python.exe -m ruff check .
~~~

자동 테스트는 임시 DB·합성 근거·모의 업로드 응답을 사용합니다. 실제 Notion 수용 시험에서는 새로운
프로세스·스레드 학습 작업을 만들고 원문 조사·교차 검토를 거쳐 다음을 확인합니다.

- 주소 공간 구조도, 동시성·병렬성 타임라인, 경쟁 상태 실행 순서를 관련 설명 옆에 배치한다.
- 요약과 대표 그림에서 핵심 차이를 찾고, 그림·캡션에서 공유 경계와 순서를 읽을 수 있다.
- 질문이 답안보다 먼저 보이며 데스크톱·좁은 화면에서도 글자와 표를 읽을 수 있다.
- 승인된 도식·표·토글·캡션이 실제 Notion 화면과 전체 재조회 결과에 보존된다.
- 작업 ID·초안 버전·두 해시·사용자 승인·생성 page ID·관찰 결과·미해결 제한을 기록한다.

이 시험이 통과하기 전까지 전체 개선을 실제 계정에서 완료했다고 보고하지 않습니다.
