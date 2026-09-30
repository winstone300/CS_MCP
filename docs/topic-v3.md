# 주제별 문서 v3

새 작업의 기본 프로필은 `study_topic_v3`다. Python은 목차 참조·버전·자산·승인 연결만
검사한다. 설명의 정확성, 학습 순서, 중복과 실제 사례 판단은 Codex가 담당한다.
새 모델 API·검색 API·인증·외부 호스팅은 추가하지 않는다.

## 구성과 책임

권장 순서는 요약·대표 그림·목표 → 주제별 정의·필요성·동작·예시·비교·주의점 → 전체 연결
시나리오·실제 사례 → 면접 키워드·질문·참고자료다. 모든 주제에 같은 하위 제목을 강제하지 않는다.
메인은 목차와 검증된 요약을 작성한다. foundation/advanced는 기존 자기 부분만 저장한다.
전체 연결 시나리오는 advanced의 concepts로 저장하고 근거·그림을 연결한다.

목차는 `type=group`(id, title, display=heading|toggle, children)과
`type=item`(section, item_id)으로 구성한다. 본문을 복사하지 않는다.
직접 참조 대상은 terms/principles/examples/concepts/tradeoffs/cases이며 정확히 한 번 배치한다.
오해·표·그림은 기존 related_item_id/placement를 따라 자동 이동한다. overview 자료는 상단,
질문과 출처는 하단이다. 서로 다른 역할은 같은 item_id를 써도 구분된다.

## 준비 → 검토 → 초안

1. 기존 방식으로 조사·기초·심화 작성과 원문 검증을 수행한다. `run-role` CLI와 최대 3개 슬롯을 유지한다.
2. 실제 읽은 양쪽 버전으로 `prepare_visual_assets`를 호출한다. v3 반환의 next_step은 전체 미리보기 준비다.
3. 메인만 `prepare_document_preview(job_id, content, expected_version)`을 호출한다.
   content는 기존 DraftInput의 title/summary/foundation_version/advanced_version에
   research_revision과 outline을 더한다. expected_version은 최초 0, 이후 실제 읽은 후보 버전이다.
4. 반환된 version/presentation_hash/content/paths와 `current=true`를 확인한다. 준비 도구는
   교차 검토나 사용자 승인을 요구하지 않고, 승인·발행도 수행하지 않는다.
5. 양쪽 검토자는 `get_document_preview(job_id, version)`으로 같은 버전을 읽고 실제 HTML의
   토글을 펼쳐 코드·표·그림·캡션·배치를 확인한다. 원문과 함께 검토한 presentation_hash 및
   실제 읽은 본문 버전·research_revision으로 `record_cross_review`한다.
6. 메인은 다음 형태로 검토한 후보만 승격한다. 새로운 제목·요약·목차를 동시에 제출하지 않는다.

```json
{
  "job_id": "<작업 ID>",
  "content": {
    "preview_version": 1,
    "presentation_hash": "<실제 읽고 검토한 후보 해시>"
  }
}
```

7. 전체 초안·그림·대상·버전·hash·bundle_hash를 사용자에게 제시한다. 명시적인 발행 답변 뒤에만
   승인을 기록하고 writer를 실행한다. 개발 구현 요청은 발행 승인이 아니다.

`get_study.document_preview`는 재개할 후보의 버전·해시를 알려준다. 전체 조회로 현재 의존성 일치 여부를
반드시 확인한다. 과거 후보 조회는 최신 후보로 치환하지 않는다. 제목·요약·목차만 바꿔도 새 후보와
검토가 필요하다. 본문·조사·자산 변경 후 기존 후보는 stale이 된다.

동일 입력·의존성으로 현재 버전을 다시 준비하면 버전과 승인을 유지하고 내보내기만 복구한다.
export_error가 반환되면 DB 후보는 이미 저장된 상태이므로 반환된 버전으로 같은 입력을 재준비한다.
검토 파일을 직접 편집하면 검증이 실패한다. 발행이 시작된 작업은 후보를 수정할 수 없다.

## 저장과 호환성

SQLite schema v3의 `document_previews`에 구성안·의존성·출력·해시를 불변 버전으로 저장한다.
미리보기 파일은 `.cs-study/previews/<job_id>/vN.{html,md,notion.md}`다.
렌더링은 쓰기 잠금 밖에서 수행하고, 저장 직전에 본문·조사·자산·후보 버전을 다시 확인한다.
후보 해시는 제목·요약·목차·본문·자료·자산·출력에 연결되며 v3 승인 묶음은
`presentation_bundle_v3`와 composition_version/plan_hash를 포함한다.

기존 v1/v2 프로필·문서 직렬화·해시 계산은 변경하지 않는다. v1/v2→schema v3 전환은 SQLite backup
API와 자산 복사로 복구본을 만들고 기존 행을 재작성하지 않는다. 현재 작업에 새로운 규칙을 소급하지 않는다.
명시적으로 `presentation_profile="study_readable_v2"` 또는 `"legacy_v1"`을 선택한 작업도 지원한다.

## 검증과 실제 Notion

로컬 자동 테스트는 합성 자료와 모의 승인·발행 응답만 사용한다. HTTP Request 미리보기는 개발용
표현 샘플이며 검증된 학습 문서나 Notion 발행 결과가 아니다.
프로젝트 루트에서 `.venv/Scripts/python.exe tests/topic_demo.py`를 실행하면
`.cs-study/previews/topic-v3-demo/index.html`과 Markdown 샘플을 다시 만들 수 있다.
실계정 수용 시험은 구체적인 초안·대상을 제시해 승인받은 후 진행한다. 중첩 토글 안의 표·코드·그림을
실제 화면에서 확인하고, 재조회한 전체 자식과 자산을 비교한 뒤에만 completed로 기록한다.
기존 생성 결과 불명확 시 중단·단일 페이지 복구·고정 발행 대상 규칙은 그대로 적용한다.
