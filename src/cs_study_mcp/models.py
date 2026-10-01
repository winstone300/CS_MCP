"""Structured handoffs. Schemas validate structure, never the truth of a claim."""

from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_serializer,
    model_validator,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
SectionKind = Literal["foundation", "advanced"]
Role = Literal["main", "research", "foundation", "advanced", "notion_writer"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Model):
    excerpt: Text
    locator: Text


class Source(Model):
    id: Identifier
    url: HttpUrl
    title: Text
    kind: Literal["official", "standard", "paper", "engineering_blog", "other"]
    accessed_at: AwareDatetime
    original_checked: Literal[True]
    evidence: list[Evidence] = Field(min_length=1)


class Claim(Model):
    id: Identifier
    text: Text
    source_ids: list[Identifier] = Field(default_factory=list)
    verdict: Literal["supported", "uncertain", "conflicting"] = "uncertain"
    verification_note: Text


class Explanation(Model):
    id: Identifier | None = Field(default=None, description="새 문서의 배치에 쓰는 역할 내 고유 ID")
    heading: Text
    key_point: Text | None = Field(default=None, description="작성자가 제공하는 짧은 핵심 문장")
    body: Text
    related_item_id: Identifier | None = Field(
        default=None, description="오해를 바로 뒤에 배치할 같은 역할의 본문 항목 ID"
    )
    claim_ids: list[Identifier] = Field(default_factory=list)


class Term(Model):
    id: Identifier | None = Field(default=None, description="새 문서의 배치에 쓰는 역할 내 고유 ID")
    name: Text
    definition: Text
    why: str = Field(default="", description="짧은 필요성. 긴 설명은 원리 항목으로 이동")
    example: str = Field(default="", description="짧은 예시. 긴 예시는 별도 예시 항목으로 이동")
    claim_ids: list[Identifier] = Field(default_factory=list)


class Illustration(Model):
    id: Identifier | None = None
    title: Text
    body: Text
    kind: Literal["hypothetical", "documented"] = "hypothetical"
    claim_ids: list[Identifier] = Field(default_factory=list)


class Question(Model):
    id: Identifier
    kind: Literal["concept", "comparison", "application"]
    question: Text
    answer: Text
    explanation: Text
    claim_ids: list[Identifier] = Field(default_factory=list)


class Tradeoff(Model):
    id: Identifier | None = None
    topic: Text
    options: Text
    advantages: Text
    limitations: Text
    choose_when: Text
    claim_ids: list[Identifier] = Field(default_factory=list)


class RealCase(Model):
    id: Identifier | None = None
    title: Text
    system: Text
    problem: Text
    technology: Text
    rationale: Text
    outcome: Text
    limitations: Text
    interpretation: Text
    claim_ids: list[Identifier] = Field(default_factory=list)


class Placement(Model):
    section: SectionKind = Field(description="자료 작성자의 역할. 타 역할 배치는 허용하지 않음")
    after_id: Identifier | None = Field(
        default=None, description="같은 역할의 안정적인 본문 항목 ID. null은 상단 overview"
    )


class TableRow(Model):
    cells: list[str] = Field(description="열 순서와 같은 순서의 셀. 빈 셀 허용")
    claim_ids: list[Identifier] = Field(
        default_factory=list, description="행 고유 근거가 있을 때 연결"
    )


class ComparisonTable(Model):
    id: Identifier
    title: Text
    columns: list[Text] = Field(min_length=1)
    rows: list[TableRow] = Field(default_factory=list)
    claim_ids: list[Identifier] = Field(default_factory=list)
    placement: Placement


class ScreenshotSource(Model):
    source_id: Identifier
    image_url: HttpUrl | None = Field(default=None, description="공개 이미지의 원본 URL. 직접 캡처는 생략")
    locator: Text = Field(description="원문 내 이미지 위치")
    accessed_at: AwareDatetime
    product_version: Text | None = None
    usage_note: Text = Field(description="인용·재사용 조건과 확인한 근거")
    capture_method: Literal["public_image", "direct_capture"] = "public_image"
    capture_url: HttpUrl | None = Field(default=None, description="직접 캡처한 웹 화면의 URL")
    capture_target: Text | None = Field(default=None, description="실습 앱·화면·실행 명령 등 재현 가능한 캡처 대상")
    captured_at: AwareDatetime | None = None
    capture_environment: Text | None = Field(default=None, description="브라우저·OS·제품 버전 또는 실습 실행 환경")

    @model_validator(mode="after")
    def check_provenance(self):
        if self.capture_method == "public_image":
            if self.image_url is None:
                raise ValueError("공개 이미지에는 원본 image_url이 필요합니다.")
            if any(value is not None for value in (
                self.capture_url, self.capture_target, self.captured_at, self.capture_environment,
            )):
                raise ValueError("직접 캡처 정보에는 capture_method=direct_capture를 사용하세요.")
        else:
            if self.image_url is not None:
                raise ValueError("직접 캡처는 image_url 대신 캡처 대상과 승인 자산 업로드를 사용합니다.")
            if (not (self.capture_url or self.capture_target)
                    or self.captured_at is None or not self.capture_environment):
                raise ValueError("직접 캡처에는 대상·captured_at·capture_environment가 필요합니다.")
        return self

    @model_serializer(mode="wrap")
    def preserve_public_image_serialization(self, handler):
        data = handler(self)
        # Added defaults must not change existing screenshot spec hashes or approvals.
        if self.capture_method == "public_image":
            for key in (
                "capture_method", "capture_url", "capture_target", "captured_at", "capture_environment",
            ):
                data.pop(key, None)
        return data


class Visual(Model):
    id: Identifier
    kind: Literal["structure", "flow", "timeline", "screenshot"]
    title: Text
    placement: Placement
    caption: Text = Field(description="그림이 설명하는 관계·순서·전제와 생략 범위")
    alt_text: Text = Field(description="그림을 보지 않아도 내용을 이해할 수 있는 대체 텍스트")
    claim_ids: list[Identifier] = Field(default_factory=list)
    diagram_spec: Text | None = Field(
        default=None, description="검토할 Mermaid 정의. 네트워크 호출 금지"
    )
    asset_hash: str | None = Field(default=None, description="불변 저장한 실제 이미지의 SHA-256")
    screenshot: ScreenshotSource | None = None


class FoundationSection(Model):
    kind: Literal["foundation"] = "foundation"
    objectives: list[Text] = Field(default_factory=list)
    prerequisites: list[Text] = Field(default_factory=list)
    terms: list[Term] = Field(default_factory=list)
    principles: list[Explanation] = Field(default_factory=list)
    examples: list[Illustration] = Field(default_factory=list)
    misconceptions: list[Explanation] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    comparisons: list[ComparisonTable] = Field(default_factory=list)
    visuals: list[Visual] = Field(default_factory=list)


class AdvancedSection(Model):
    kind: Literal["advanced"] = "advanced"
    foundation_version: int = Field(ge=1)
    concepts: list[Explanation] = Field(default_factory=list)
    tradeoffs: list[Tradeoff] = Field(default_factory=list)
    cases: list[RealCase] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    comparisons: list[ComparisonTable] = Field(default_factory=list)
    visuals: list[Visual] = Field(default_factory=list)


KnowledgeSection = Annotated[FoundationSection | AdvancedSection, Field(discriminator="kind")]


class Finding(Model):
    severity: Literal["blocking", "advisory"]
    location: Text
    comment: Text


class CrossReview(Model):
    reviewer: SectionKind
    foundation_version: int = Field(ge=1)
    advanced_version: int = Field(ge=1)
    research_revision: int = Field(ge=0)
    summary: Text
    findings: list[Finding] = Field(default_factory=list)
    presentation_hash: str | None = Field(
        default=None, description="실제 검토한 부분 문서와 렌더링 자산 묶음의 해시"
    )


class SummaryPoint(Model):
    text: Text
    section: SectionKind
    claim_ids: list[Identifier] = Field(min_length=1)


class DraftInput(Model):
    title: Text
    foundation_version: int = Field(ge=1)
    advanced_version: int = Field(ge=1)
    summary: list[SummaryPoint] = Field(min_length=1)


class OutlineItemRef(Model):
    type: Literal["item"] = "item"
    section: SectionKind
    item_id: Identifier


class OutlineGroup(Model):
    type: Literal["group"] = "group"
    id: Identifier
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^\r\n<>]+$")]
    display: Literal["heading", "toggle"] = "toggle"
    children: list[Annotated["OutlineGroup | OutlineItemRef", Field(discriminator="type")]] = Field(min_length=1)


class DocumentPlanInput(DraftInput):
    """A v3 composition; never add fields to historical section/draft serialization."""

    research_revision: int = Field(ge=0)
    outline: list[Annotated[OutlineGroup | OutlineItemRef, Field(discriminator="type")]] = Field(min_length=1)


class PreviewReference(Model):
    preview_version: int = Field(ge=1)
    presentation_hash: Text


class ReviewInput(Model):
    draft_version: int = Field(ge=1)
    draft_hash: Text
    parent_page_id: Text
    decision: Literal["approve", "request_changes"]
    user_message: Text
    feedback: str = ""
    bundle_hash: str | None = Field(
        default=None, description="사용자에게 제시한 v2 승인 묶음의 해시"
    )


class PublicationInput(Model):
    attempt_id: Text
    outcome: Literal["page_created", "verified", "retryable_error", "uncertain", "auth_required"]
    page_id: str | None = None
    page_url: HttpUrl | None = None
    observed_title: str | None = None
    observed_parent_page_id: str | None = None
    observed_markdown: str | None = None
    observed_blocks: list[dict] | None = Field(default=None, description="실제 재조회한 본문 블록")
    observed_assets: list[dict] = Field(
        default_factory=list, description="실제 재조회한 자산 식별·해시 근거"
    )
    observed_visual_check: bool = Field(
        default=False,
        deprecated=True,
        description="이전 클라이언트 호환용 필드. 값은 완료 판정에 사용하지 않으며 화면 확인은 요구하지 않습니다.",
    )
    error: str | None = None
