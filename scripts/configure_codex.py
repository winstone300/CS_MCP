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
legacy_v1 작업에는 새 형식의 필드를 요구하지 않는다. study_readable_v2와 study_topic_v3에 아래 공통 지침을 적용한다.
이미지·출처 표기는 저장된 프로필과 규칙을 따른다. 새 작업에서 이미지가 이해에 도움이 되면 관련 본문에 실제 이미지를 첨부하고 출처 링크만으로 대신하지 않는다.
새 작업의 본문·요약·표·답안·그림 캡션에는 출처 번호나 링크를 직접 쓰지 않는다. claim_ids/source_ids와 이미지 근거 메타데이터는 유지하고 참고 사이트·문헌·이미지 출처는 마지막 참고 문헌에 모은다.
"""
PRESENTATION = {
    "research": """도식의 경계·관계·순서·전제와 비교표에 필요한 원문 근거를 수집한다.
공식 공개 이미지를 우선 조사하고 source ID, 원문 위치, 이미지 URL, 확인 시점·제품 버전·이용 조건을 반환한다.
적절한 공개 이미지가 없으면 공개 웹 화면 또는 재현 가능한 실습의 캡처 대상·절차·환경·근거·이용 조건을 메인에게 전달한다.
직접 캡처와 프로젝트 내부 저장·register_visual_asset 등록은 메인이 담당한다. research는 캡처·사진 생성·외부 호스팅을 수행하지 않는다.""",
    "foundation": """설명에는 안정 ID와 key_point를 부여하고 핵심 문장→이유→예시 순으로 작성한다.
용어는 짧은 정의로 시작하고 필요성과 예시를 관련 본문에 배치해 중복을 줄인다.
대표 구조도·기초 비교표는 자신의 항목에 연결하고 표·그림의 claim_ids를 명시한다.
예시 이해에 도움이 되는 실제 이미지를 메인에게 요청하고 등록된 자산을 관련 항목의 visuals에 연결한다. 화면을 확보하지 못하면 생략 이유를 본문에 기록하고 텍스트·도식으로 설명한다.
오해에는 related_item_id를 넣는다. 재정렬에도 항목 ID를 유지한다. 초기 미완성 초안 저장은 허용한다.
메인에 prepare_visual_assets를 요청하고 반환된 실제 그림·캡션·표를 원문과 교차 검토한다.
화살표·경계·생략과 의미상 중복을 확인하고 record_cross_review에 실제 읽은 presentation_hash를 전달한다.""",
    "advanced": """설명에는 안정 ID와 key_point를 부여하고 한 문단에 한 개념을 작성한다.
내부 실행 순서·타임라인·선택 기준은 자신의 항목에 연결한 도식·표로 설명한다.
표·그림은 claim_ids에 연결하고 전제와 확인된 사실을 해석과 구분한다. 재정렬에도 ID를 유지한다.
예시·실제 사례 이해에 도움이 되는 실제 이미지를 메인에게 요청하고 등록된 자산을 관련 항목의 visuals에 연결한다. 화면을 확보하지 못하면 생략 이유를 본문에 기록하고 텍스트·도식으로 설명한다.
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
Notion 브라우저 화면 검증은 수행하거나 완료 조건으로 요구하지 않는다.
실제 재조회한 제목·상위 페이지·전체 본문 구조·자산이 일치해 completed가 된 경우에만 완료를 보고한다.
표시 형식을 바꾸려면 새 초안과 승인이 필요하다.""",
}

VISUAL_ASSESSMENT_COMMON = """get_document_blueprint의 저장된 profile.visual_assessment_policy_version=1인 신규 작업에만 아래 절차를 적용한다. 값이 없는 기존 v1/v2/v3 작업에는 새 필드·검사·검토를 요구하지 않는다.
표현의 학습 가치·관찰 목표·대체 설명의 충분성은 Codex가 판단한다. Python은 구조·참조·버전·자산만 검사한다.
이미지 확보 실패와 이미지 불필요 판단을 구분한다. 등록 이미지가 없다는 사실이나 확보 불가 기록만으로 생략을 정당화하지 않는다. 핵심 주장·실제 사례의 근거가 부족하면 통합을 막는다.
"""
VISUAL_ASSESSMENTS = {
    "research": "최초 조사에 공식 이미지 후보·캡처 대상·재현 절차·환경·이용 조건을 포함한다. 작성 역할의 관찰 목표를 확인하고 기존 근거로 처리 가능한 요청을 구분한다. 추가 조사는 메인의 request_research_followup 허가와 작업 전체 최대 2회 제한을 따르며 항목별로 반복하지 않는다.",
    "foundation": "foundation.examples 각 항목에 정확히 하나의 visual_assessments를 저장한다. 다른 설명도 필요하면 평가한다. item_id·learning_goal·preferred_kind·rationale·source_ids·acquisition_plan을 기록하며 초안의 pending은 허용된다. 메인의 실제 결과는 get_visual_acquisitions/get_study로 읽고 result_ids에 연결한다. 자신의 부분에만 selected_kind·visual_ids 또는 explanation_item_id·change_reason·reader_note를 반영하고 status=resolved로 저장한다. 변경된 대상·요청에 오래된 확보 결과를 재사용하지 않는다. 최종 get_document_preview의 동일 후보 평가표와 확보 결과 전체를 실제 읽고 이미지·도식·텍스트의 적합성, 생략·대체 이유, 핵심 근거 유지 여부를 검토한다. record_cross_review.reviewed_visual_assessments에 양쪽 모든 평가의 section/assessment_id를 기록한다. reader_note는 발행 본문에 포함되며 내부 시도 로그는 본문에 복사하지 않는다.",
    "advanced": "advanced.cases 각 항목에 정확히 하나의 visual_assessments를 저장한다. 다른 설명도 필요하면 평가한다. item_id·learning_goal·preferred_kind·rationale·source_ids·acquisition_plan과 실제 사례 사실/재현 실습의 차이를 명확히 한다. 메인의 실제 결과를 get_visual_acquisitions/get_study로 읽고 result_ids에 연결한다. 자신의 부분에만 selected_kind·visual_ids 또는 explanation_item_id·change_reason·reader_note를 반영하고 status=resolved로 저장한다. 대상·요청 변경 시 결과를 다시 확인한다. 기초 변경 후 최신 foundation_version과 내용을 갱신한다. get_document_preview의 동일 후보 평가표·확보 결과 전체를 실제 읽고 표현의 적합성·대체 이유·핵심 근거 유지 여부를 검토한다. record_cross_review.reviewed_visual_assessments에 양쪽 모든 평가의 section/assessment_id를 기록한다. reader_note는 발행 본문에 포함되며 내부 시도 로그는 본문에 복사하지 않는다.",
    "notion_writer": "판단·채택한 확보 결과·본문·자산이 포함된 승인 묶음을 유지한다. reader_note의 실습 전제·생략 설명도 관련 본문에서 보존해 실제 재조회로 확인한다. 확보 결과나 표현 선택을 대신 변경하지 않는다. 승인된 자산만 기존 업로드·재개 절차로 처리한다.",
}

KOREAN_EXPRESSION_REVIEW_COMMON = """get_document_blueprint의 저장된 profile.korean_expression_review_version=1인 신규 study_topic_v3 작업에만 한국어 표현 검수를 적용한다. 값이 없는 기존 v1/v2/v3 작업에는 새 검수 필드·완료 조건을 요구하지 않는다.
독자가 읽는 제목·요약·목차·목표·선수지식·양쪽 본문·용어의 필요성/예시·흔한 오해·사례 interpretation·표 셀·그림 캡션·alt_text·reader_note·질문·답안을 같은 최종 후보에서 검수한다. 제목·요약의 ref.item_id는 null이며 목차 제목은 그룹의 안정 ID를 사용한다. field는 summary.0.text나 rows.0.cells.1 같은 점 경로이며 해당 후보에서만 유효하므로 반환된 ref를 그대로 사용한다.
코드 구문·URL·출처 제목/발췌·실행 명령·제품 버전·인용한 영어 원문은 보존한다. 본문 속 영어가 직접 인용인지 원문을 대조해 판단하며 영어 비율로 검토 대상을 임의 제외하지 않는다. Mermaid 내부 한국어 라벨은 korean_expression_targets 추출 밖이므로 기존 시각 교차 검토에서 표현도 확인한다. 정착된 기술 용어는 유지하고 첫 등장에 쉬운 설명을 붙이며 모호할 때 원어를 병기한다.
가독성·자연스러움과 원문 의미 보존은 Codex가 판단한다. Python은 정책·검토 범위·후보·참조만 검사하며 검수 기록이 의미의 정확성을 입증하지 않는다. 의미 변화나 개념 이해를 방해하는 문제는 blocking, 이해 가능한 문장의 문체 개선은 advisory다. problem_kind=meaning_change는 severity=blocking과 실제 원문 evidence_refs가 필수다.
"""
KOREAN_EXPRESSION_REVIEWS = {
    "research": "기술적으로 중요한 영어 표현의 원문 발췌·앞뒤 문맥·위치를 기존 sources의 evidence에 보존한다. 가능성·의무·권고·조건·예외·인과관계를 확인할 수 있게 제공하며 검색 요약을 원문으로 대신하지 않는다. 작성·검토 역할에 필요한 근거가 없으면 메인에 알린다. 추가 조사 요청은 메인이 모아 request_research_followup을 호출하며 작업 전체 최대 2회 한도를 유지한다.",
    "foundation": "초안 작성 시 자기 부분의 한국어를 점검한다. 최종 교차 검토에서는 get_document_preview의 current=true인 지정 후보와 korean_expression_targets 전체를 실제 읽고 양쪽 본문뿐 아니라 문서 공통 영역도 readability 기준으로 검토한다. 직역체·긴 문장·주어/대상/지시어의 모호성·용어 풀이·표기 일관성을 확인한다. record_cross_review.korean_expression_review에는 policy_version=1, focus=readability, reviewed_targets=후보의 모든 ref를 기록한다. 지적은 기존 findings에 severity·location·comment를 기록하고 expression_detail의 target, problem_kind, current_text(후보의 실제 문제 구절), suggested_text를 제공한다. meaning_change 지적에는 실제 원문 evidence_refs(source_id/evidence_index)를 연결한다. 상대 본문은 수정하지 않고 원작성자에게 의견을 반환하며 제목·요약·목차·용어 통일 의견은 메인에게 반환한다. 수정 후 새 후보를 양쪽이 다시 검토한다.",
    "advanced": "초안 작성 시 자기 부분의 한국어와 원문 의미 보존을 점검한다. 최종 교차 검토에서는 get_document_preview의 current=true인 지정 후보와 korean_expression_targets 전체를 실제 읽고 양쪽 본문과 문서 공통 영역을 meaning 기준으로 검토한다. 저장된 주장·원문 근거를 대조해 가능성·의무·권고·조건·예외·인과관계·비교 대상이 바뀌지 않았는지 확인한다. record_cross_review.korean_expression_review에는 policy_version=1, focus=meaning, reviewed_targets=후보의 모든 ref를 기록한다. 지적은 기존 findings에 severity·location·comment를 기록하고 expression_detail의 target, problem_kind, current_text(후보의 실제 문제 구절), suggested_text를 제공한다. meaning_change 지적에는 실제 원문 evidence_refs(source_id/evidence_index)를 반드시 연결한다. 원문이 부족하면 추측하지 않고 메인에 근거 보완을 요청한다. 상대 본문과 메인 공통 영역은 직접 수정하지 않는다. 기초 변경 시 foundation_version을 갱신하고 새 후보를 양쪽이 다시 검토한다.",
    "notion_writer": "한국어 표현 검수가 연결된 승인 후보의 제목·요약·본문·용어·표·캡션·alt·실습 전제·생략 설명·질문·답안을 변경 없이 발행한다. 어색한 표현을 발견해도 승인 후 직접 고치지 않고 메인에 보고한다. 실제 재조회로 승인된 표현과 본문 구조가 보존됐는지 확인한다.",
}


def presentation_instructions(role: str) -> str:
    return (
        "[CS-STUDY PRESENTATION V2]\n"
        + PRESENTATION_COMMON
        + PRESENTATION[role]
        + "\n[/CS-STUDY PRESENTATION V2]"
        + "\n\n[CS-STUDY TOPIC V3]\n"
        + "아래 지침은 study_topic_v3에만 적용한다. 기존 작업은 저장된 규칙과 프로필을 유지한다.\n"
        + {
            "research": "메인이 정한 주제별 목차의 정의·필요성·동작·비교·전체 연결 시나리오 근거를 조사한다. 참고 문서의 표현을 사실 검증 없이 복사하지 않는다.",
            "foundation": "정의→필요성→원리→구체 예시 순서의 짧은 본문을 안정 ID로 작성한다. 긴 설명을 용어집에 몰아넣지 않는다. 메인이 prepare_document_preview로 준비한 지정 버전을 get_document_preview로 읽고, Markdown 전체 본문과 중첩 토글 자식·코드·표·도식·캡션·연결된 그림·순서를 확인한다. HTML 파일 생성이나 브라우저 화면 검증은 요구하지 않는다. current=true인 같은 후보의 presentation_hash로 교차 검토한다. 목차 변경 의견은 메인에, 본문 변경 의견은 원작성자에 반환한다.",
            "advanced": "기초 항목 뒤에 연결할 심화·비교·한계·실제 사례를 안정 ID로 작성한다. 전체 개념 연결 시나리오는 자신의 concepts와 그림에 근거를 연결해 작성한다. 기술 예시는 body의 코드펜스, 흐름은 Mermaid를 사용한다. get_document_preview로 지정 버전의 Markdown과 양쪽 본문을 실제 읽고 중첩 토글 자식·코드·표·도식·캡션·연결된 그림·순서를 검토한다. HTML 파일 생성이나 브라우저 화면 검증은 요구하지 않는다. current=true인 같은 후보의 presentation_hash를 전달한다. 메인의 목차나 상대 본문을 직접 수정하지 않는다.",
            "notion_writer": "승인 묶음의 composition_version과 plan_hash를 보존한다. 제목·목차·중첩 토글·본문·표·그림·코드·답안을 바꾸지 않는다. 실제 도구 재조회에서는 중첩된 자식을 모두 보존해 비교한다. Notion 화면 검증은 요구하지 않는다. 로컬 Markdown 검토를 실제 Notion 재조회로 대신하지 않는다.",
        }[role]
        + "\n" + VISUAL_ASSESSMENT_COMMON + VISUAL_ASSESSMENTS[role]
        + "\n" + KOREAN_EXPRESSION_REVIEW_COMMON + KOREAN_EXPRESSION_REVIEWS[role]
        + "\n[/CS-STUDY TOPIC V3]"
    )


def upgrade_presentation_prompt(path: Path, role: str) -> None:
    text = path.read_text(encoding="utf-8-sig")
    current = tomllib.loads(text)["developer_instructions"]
    current = re.sub(
        r"\n?\[CS-STUDY PRESENTATION V2\].*?\[/CS-STUDY PRESENTATION V2\]", "", current, flags=re.S
    ).rstrip()
    current = re.sub(r"\n?\[CS-STUDY TOPIC V3\].*?\[/CS-STUDY TOPIC V3\]", "", current, flags=re.S).rstrip()
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
    remove_project_usage_hooks(root)
    disable_project_hooks(root)
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


def remove_project_usage_hooks(root: Path) -> None:
    """Migrate only our old marked hooks; preserve all user-owned hook sources."""
    path = root / ".codex/config.toml"
    text = path.read_text(encoding="utf-8-sig")
    pattern = r"(?m)^(?:\r?\n)?# BEGIN CS-STUDY MANAGED USAGE HOOKS\r?\n.*?^# END CS-STUDY MANAGED USAGE HOOKS(?:\r?\n|$)"
    updated, count = re.subn(pattern, "", text, flags=re.S)
    if not count:
        return
    tomllib.loads(updated)
    path.write_text(updated, encoding="utf-8")


def disable_project_hooks(root: Path) -> None:
    """Disable hooks in ordinary chats without replacing other feature flags."""
    path = root / ".codex/config.toml"
    text = path.read_text(encoding="utf-8-sig")
    config = tomllib.loads(text)
    if config.get("features", {}).get("hooks") is False:
        return
    table = re.search(r"(?m)^\[features\][ \t]*(?:#.*)?$", text)
    if table:
        next_table = re.search(r"(?m)^\[", text[table.end():])
        end = table.end() + next_table.start() if next_table else len(text)
        body = text[table.end():end]
        body, count = re.subn(r"(?m)^(hooks\s*=\s*)(?:true|false)\b", r"\1false", body)
        if not count:
            body = "\nhooks = false" + body
        updated = text[:table.end()] + body + text[end:]
    elif re.search(r"(?m)^features\.hooks\s*=", text):
        updated = re.sub(r"(?m)^(features\.hooks\s*=\s*)(?:true|false)\b", r"\1false", text)
    elif re.search(r"(?m)^features\.", text):
        updated = "features.hooks = false\n" + text
    elif "features" not in config:
        updated = text.rstrip() + "\n\n[features]\nhooks = false\n"
    else:
        raise ValueError(f"features 설정을 안전하게 병합하지 못했습니다: {path}")
    parsed = tomllib.loads(updated)
    if parsed.get("features", {}).get("hooks") is not False:
        raise ValueError(f"hooks 비활성화 설정을 확인하지 못했습니다: {path}")
    path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--upgrade-presentation",
        action="store_true",
        help="기존 역할의 사용자 지침을 보존하며 v2/v3 문서·시각 자료·한국어 검수 지침만 병합",
    )
    args = parser.parse_args()
    configure(args.project, args.upgrade_presentation)
    print("Codex 프로젝트 구성 완료. 변경된 설정은 새 Codex 세션에서 확인하세요.")
