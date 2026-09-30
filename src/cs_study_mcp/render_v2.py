"""Readable document rendering from an immutable authored bundle.

The renderer only arranges authored content; it does not summarize or deduplicate it.
The legacy renderer in render.py intentionally stays unchanged.
"""

import re
from html import escape
from pathlib import Path

from .models import AdvancedSection, DraftInput, FoundationSection, OutlineGroup, Source


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", r"\[").replace("]", r"\]")


def _cell(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace("|", r"\|").replace("\r\n", "\n").replace("\n", "<br>")
    )


def render_readable(
    draft: DraftInput,
    foundation: FoundationSection,
    advanced: AdvancedSection,
    sources: dict[str, Source],
    profile: dict,
    assets: list[dict],
    *,
    outline=None,
) -> dict:
    from .publication import canonicalize_markdown

    lines: list[str] = []
    citation_numbers: dict[str, int] = {}
    assets_by_visual = {(item.get("section"), item.get("visual_id")): item for item in assets}
    assets_by_hash = {item.get("hash", item.get("asset_hash")): item for item in assets}
    diagram_paths: dict[str, tuple[str, str]] = {}
    notion_overrides: dict[str, str] = {}

    def block(text: str) -> None:
        if text:
            lines.extend([text, ""])

    def source_cite(source_ids: list[str]) -> str:
        rendered = []
        for sid in sorted(set(source_ids)):
            if sid not in citation_numbers:
                citation_numbers[sid] = len(citation_numbers) + 1
            rendered.append(f"[{citation_numbers[sid]}]({sources[sid].url})")
        return " ".join(rendered)

    def cite(section: FoundationSection | AdvancedSection, claim_ids: list[str]) -> str:
        claims = {claim.id: claim for claim in section.claims}
        return source_cite([sid for cid in claim_ids for sid in claims[cid].source_ids])

    def body(section: FoundationSection | AdvancedSection, text: str, claim_ids: list[str]) -> None:
        block(text)
        block(cite(section, claim_ids))

    def materials(section: FoundationSection | AdvancedSection, after_id: str | None) -> None:
        # Separate arrays have a fixed table-then-visual order; each array preserves author order.
        for table in section.comparisons:
            if table.placement.after_id != after_id:
                continue
            block(f"### {table.title}")
            rows = ["| " + " | ".join(_cell(value) for value in table.columns) + " |"]
            rows.append("| " + " | ".join("---" for _ in table.columns) + " |")
            table_cells = [[_cell(value) for value in table.columns]]
            for row in table.rows:
                cells = [_cell(value) for value in row.cells]
                if row.claim_ids:
                    cells[-1] += " " + cite(section, row.claim_ids)
                table_cells.append(cells)
                rows.append("| " + " | ".join(cells) + " |")
            markdown_table = "\n".join(rows)
            enhanced = ['<table header-row="true">']
            for cells in table_cells:
                enhanced.append("\t<tr>")
                for value in cells:
                    escaped = escape(value, quote=False).replace("&lt;br&gt;", "<br>")
                    enhanced.append(f"\t\t<td>{escaped}</td>")
                enhanced.append("\t</tr>")
            enhanced.append("</table>")
            notion_overrides[markdown_table] = "\n".join(enhanced)
            block(markdown_table)
            block(cite(section, table.claim_ids))
        for visual in section.visuals:
            if visual.placement.after_id != after_id:
                continue
            asset = assets_by_visual.get((section.kind, visual.id), {})
            asset_hash = asset.get("hash", asset.get("asset_hash", visual.asset_hash))
            path = asset.get("path")
            block(f"### {visual.title}")
            if (
                visual.kind != "screenshot"
                and profile.get("diagram_transport", "mermaid") == "mermaid"
            ):
                spec = visual.diagram_spec or ""
                fence = "`" * max(
                    3, max((len(value) + 1 for value in re.findall(r"`+", spec)), default=3)
                )
                block(f"{fence}mermaid\n{spec}\n{fence}")
                if path:
                    diagram_paths[spec.strip()] = (str(path), visual.alt_text)
            else:
                if not asset_hash:
                    raise ValueError(f"그림의 승인용 자산이 없습니다: {section.kind}.{visual.id}")
                markdown_image = f"![{_label(visual.alt_text)}](asset://{asset_hash})"
                block(markdown_image)
                if visual.kind == "screenshot" and visual.screenshot:
                    notion_overrides[markdown_image] = (
                        f"![{_label(visual.alt_text)}]({visual.screenshot.image_url})"
                    )
            block(f"**그림 설명:** {visual.alt_text}")
            body(section, visual.caption, visual.claim_ids)
            if visual.screenshot:
                screenshot = visual.screenshot
                version = (
                    f" · 제품 버전 {screenshot.product_version}"
                    if screenshot.product_version
                    else ""
                )
                block(
                    f"**공개 스크린샷:** {source_cite([screenshot.source_id])} · {screenshot.locator} · 확인일 {screenshot.accessed_at.date().isoformat()}{version}"
                )
                block(f"**이용 조건:** {screenshot.usage_note}")

    def after(section: FoundationSection | AdvancedSection, item_id: str | None) -> None:
        if item_id is None:
            return
        if isinstance(section, FoundationSection):
            for misconception in section.misconceptions:
                if misconception.related_item_id == item_id:
                    block(f"#### 흔한 오해: {misconception.heading}")
                    if misconception.key_point:
                        block(f"**{misconception.key_point}**")
                    body(section, misconception.body, misconception.claim_ids)
                    materials(section, misconception.id)
        materials(section, item_id)

    block("## 핵심 요약")
    for point in draft.summary:
        section = foundation if point.section == "foundation" else advanced
        block(f"- {point.text} {cite(section, point.claim_ids)}")
    materials(foundation, None)
    materials(advanced, None)
    block("## 학습 목표·선수지식")
    for objective in foundation.objectives:
        block(f"- {objective}")
    block("**선수지식:** " + (", ".join(foundation.prerequisites) or "별도 선수지식 없음"))
    introduction = list(lines)
    item_blocks = {}
    block("## 핵심 용어")
    for term in foundation.terms:
        start = len(lines)
        block(f"### {term.name}")
        body(foundation, term.definition, term.claim_ids)
        if term.why:
            block(f"**필요한 이유:** {term.why}")
        if term.example:
            block(f"**짧은 예시:** {term.example}")
        after(foundation, term.id)
        item_blocks[("foundation", term.id)] = lines[start:]
    block("## 기본 원리·쉬운 예시")
    for item in foundation.principles:
        start = len(lines)
        block(f"### {item.heading}")
        if item.key_point:
            block(f"**{item.key_point}**")
        body(foundation, item.body, item.claim_ids)
        after(foundation, item.id)
        item_blocks[("foundation", item.id)] = lines[start:]
    for item in foundation.examples:
        start = len(lines)
        label = "가상 설명용 예시" if item.kind == "hypothetical" else "출처로 확인한 예시"
        block(f"### {item.title} ({label})")
        body(foundation, item.body, item.claim_ids)
        after(foundation, item.id)
        item_blocks[("foundation", item.id)] = lines[start:]
    block("## 심화 동작·장단점")
    for item in advanced.concepts:
        start = len(lines)
        block(f"### {item.heading}")
        if item.key_point:
            block(f"**{item.key_point}**")
        body(advanced, item.body, item.claim_ids)
        after(advanced, item.id)
        item_blocks[("advanced", item.id)] = lines[start:]
    for item in advanced.tradeoffs:
        start = len(lines)
        block(f"### {item.topic}")
        for label, value in [
            ("선택지", item.options),
            ("장점", item.advantages),
            ("한계", item.limitations),
            ("선택 기준", item.choose_when),
        ]:
            block(f"**{label}:** {value}")
        block(cite(advanced, item.claim_ids))
        after(advanced, item.id)
        item_blocks[("advanced", item.id)] = lines[start:]
    block("## 실제 사례")
    for case in advanced.cases:
        start = len(lines)
        block(f"### {case.title}")
        for label, value in [
            ("대상 시스템", case.system),
            ("문제 상황", case.problem),
            ("선택한 기술", case.technology),
            ("선택 이유", case.rationale),
            ("확인된 결과", case.outcome),
            ("적용 한계", case.limitations),
        ]:
            block(f"**{label}:** {value}")
        block("**확인된 사실의 근거:** " + cite(advanced, case.claim_ids))
        block("**학습을 위한 해석:** " + case.interpretation)
        after(advanced, case.id)
        item_blocks[("advanced", case.id)] = lines[start:]
    if outline is not None:
        # Convert materials before adding structural tabs, including nested HTML tables.
        def arrange(nodes, depth=0):
            arranged = []
            for node in nodes:
                if isinstance(node, OutlineGroup):
                    children = arrange(node.children, depth + 1)
                    title = escape(node.title, quote=False)
                    if node.display == "heading":
                        arranged.extend([f"{'#' * min(6, depth + 2)} {title}", "", *children])
                    else:
                        nested = "\n".join("\t" + line for line in "\n".join(children).split("\n"))
                        arranged.extend([f"<details>\n<summary>{title}</summary>\n{nested}\n</details>", ""])
                else:
                    arranged.extend(notion_overrides.get(line, line)
                                    for line in item_blocks[(node.section, node.item_id)])
            return arranged

        lines = introduction + arrange(outline)
    common = list(lines)
    block("## 면접 질문")
    questions = [
        (label, section, i, question)
        for label, section in [("기초", foundation), ("심화", advanced)]
        for i, question in enumerate(section.questions, 1)
    ]
    for label, _, i, question in questions:
        block(f"### {label} {i}. {question.question}")
    block("## 면접 답안·해설")
    for label, section, i, question in questions:
        block(f"### {label} {i}. {question.question}")
        body(
            section,
            f"**모범답안:** {question.answer}\n\n**해설:** {question.explanation}",
            question.claim_ids,
        )
    markdown_lines = list(lines)
    lines = common
    block("## 면접 질문")
    for label, section, i, question in questions:
        block(f"### {label} {i}. {question.question}")
        answer = f"**모범답안:** {question.answer}\n\n**해설:** {question.explanation}\n\n{cite(section, question.claim_ids)}"
        notion_answer = "\n".join("\t" + line for line in answer.split("\n"))
        block(f"<details>\n<summary>답안·해설</summary>\n{notion_answer}\n</details>")
    references = ["## 참고자료", ""]
    for sid, number in citation_numbers.items():
        source = sources[sid]
        references.extend(
            [
                f"- [{number}: {_label(source.title)}]({source.url}) — 확인일 {source.accessed_at.date().isoformat()}",
                "",
            ]
        )
    notion_lines = [notion_overrides.get(line, line) for line in lines]
    notion_markdown = "\n".join([*notion_lines, *references]).strip() + "\n"
    markdown = f"# {draft.title}\n\n" + "\n".join([*markdown_lines, *references]).strip() + "\n"
    blocks = canonicalize_markdown(notion_markdown)
    return {
        "markdown": markdown,
        "notion_markdown": notion_markdown,
        "blocks": blocks,
        "html": _html_document(draft.title, blocks, diagram_paths, assets_by_hash),
    }


def _inline(value: str) -> str:
    """A small escaped inline renderer; unrecognized markup remains visible text."""
    value = re.sub(r"\\([\\|\[\]])", r"\1", value)
    pattern = re.compile(r"(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?://[^)\s]+\)|<br\s*/?>)")
    result: list[str] = []
    cursor = 0
    for match in pattern.finditer(value):
        result.append(escape(value[cursor : match.start()]).replace("\n", "<br>"))
        token = match.group()
        if token.startswith("**"):
            result.append("<strong>" + escape(token[2:-2]) + "</strong>")
        elif token.startswith("`"):
            result.append("<code>" + escape(token[1:-1]) + "</code>")
        elif token.startswith("["):
            label, url = token[1:].split("](", 1)
            result.append(f'<a href="{escape(url[:-1], quote=True)}">{escape(label)}</a>')
        else:
            result.append("<br>")
        cursor = match.end()
    result.append(escape(value[cursor:]).replace("\n", "<br>"))
    return "".join(result)


def _local_uri(path: str) -> str:
    return Path(path).resolve().as_uri()


def _html_document(
    title: str, blocks: list[dict], diagram_paths: dict[str, tuple[str, str]], assets_by_hash: dict
) -> str:
    def figure(path: str, alt_text: str, *, diagram: bool = False) -> str:
        uri = escape(_local_uri(path), quote=True)
        style = ""
        if diagram:
            # Mermaid PNGs use deviceScaleFactor=2. Keep their logical size on
            # narrow screens instead of shrinking every label to fit the page.
            width = 640
            try:
                with Path(path).open("rb") as source:
                    header = source.read(24)
                if header.startswith(b"\x89PNG\r\n\x1a\n") and header[12:16] == b"IHDR":
                    width = max(360, int.from_bytes(header[16:20], "big") / 2)
            except OSError:
                pass  # Missing/unsupported assets are reported by validation.
            style = f' style="width:{width:g}px"'
        return (
            '<figure class="visual">'
            '<div class="visual-scroll" tabindex="0" role="region" aria-label="그림: 좌우 스크롤 가능">'
            f'<a class="visual-image-link" href="{uri}" target="_blank" rel="noopener noreferrer">'
            f'<img src="{uri}" alt="{escape(alt_text, quote=True)}"{style}></a></div>'
            "<figcaption>"
            f'<a href="{uri}" target="_blank" rel="noopener noreferrer">이미지 크게 보기</a>'
            "<span>좁은 화면에서는 그림을 좌우로 스크롤할 수 있습니다.</span>"
            "</figcaption></figure>"
        )

    def render(block: dict) -> str:
        kind = block["type"]
        if kind == "heading":
            level = min(6, max(1, int(block["level"])))
            return f"<h{level}>{_inline(block['text'])}</h{level}>"
        if kind == "paragraph":
            return f"<p>{_inline(block['text'])}</p>"
        if kind == "list":
            tag = "ol" if block.get("ordered") else "ul"
            return (
                f"<{tag}>"
                + "".join(f"<li>{_inline(item)}</li>" for item in block["items"])
                + f"</{tag}>"
            )
        if kind == "list_item":
            tag = "ol" if block.get("ordered") else "ul"
            marker = int(block.get("marker") or 1)
            indent = max(0, min(20, int(block.get("indent", 0))))
            start = f' start="{marker}"' if block.get("ordered") else ""
            return f'<{tag}{start} style="margin-left:{indent}ch"><li>{_inline(block["text"])}</li></{tag}>'
        if kind == "code":
            spec = block.get("text", block.get("code", ""))
            diagram = (
                diagram_paths.get(spec.strip()) if block.get("language") == "mermaid" else None
            )
            if diagram:
                path, alt_text = diagram
                return figure(path, alt_text, diagram=True)
            return f"<pre><code>{escape(spec)}</code></pre>"
        if kind == "table":
            columns = block.get("columns", block.get("header", []))
            rows = block.get("rows", [])
            head = "".join(f"<th>{_inline(value)}</th>" for value in columns)
            cells = "".join(
                "<tr>" + "".join(f"<td>{_inline(value)}</td>" for value in row) + "</tr>"
                for row in rows
            )
            minimum_width = max(320, len(columns) * 160)
            return (
                '<div class="table-scroll" tabindex="0" role="region" aria-label="비교표: 좌우 스크롤 가능">'
                f'<table style="min-width:{minimum_width}px"><thead><tr>{head}</tr></thead>'
                f'<tbody>{cells}</tbody></table></div>'
            )
        if kind == "toggle":
            return (
                f"<details><summary>{_inline(block['title'])}</summary>"
                + "".join(render(child) for child in block["children"])
                + "</details>"
            )
        if kind == "image":
            ref = block.get("ref", block.get("url", ""))
            asset_hash = ref.removeprefix("asset://")
            asset = assets_by_hash.get(asset_hash) or next(
                (item for item in assets_by_hash.values() if item.get("remote_url") == ref), {}
            )
            if not asset.get("path"):
                return f"<p>이미지 미리보기 자산 없음: {escape(ref)}</p>"
            return figure(
                asset["path"],
                block.get("alt", ""),
                diagram=asset.get("kind") in {"structure", "flow", "timeline"},
            )
        if kind == "quote":
            return (
                "<blockquote>"
                + "".join(render(child) for child in block["children"])
                + "</blockquote>"
            )
        if kind == "rule":
            return "<hr>"
        raise ValueError(f"지원하지 않는 미리보기 블록: {kind}")

    content = "\n".join(render(block) for block in blocks)
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src file: data:; style-src 'unsafe-inline'">
<title>{escape(title)}</title><style>
body{{font-family:"Malgun Gothic","Noto Sans KR",sans-serif;line-height:1.8;color:#1c2733;background:#f4f6f8;margin:0}}
main{{max-width:860px;margin:32px auto;padding:40px;background:white;border-radius:12px}}h1{{font-size:2rem;line-height:1.35}}
h2{{margin-top:2.5rem;padding-top:1rem;border-top:1px solid #dbe1e8}}h3{{margin-top:1.8rem}}p{{margin:1rem 0;overflow-wrap:anywhere}}
a{{color:#1767a6}}figure{{margin:1.5rem 0;max-width:100%;min-width:0}}table{{border-collapse:collapse;width:100%;table-layout:fixed;font-size:.95rem}}
.visual-scroll{{max-width:100%;overflow-x:auto;overscroll-behavior-x:contain;border:1px solid #dbe1e8;border-radius:6px}}
.visual-scroll:focus-visible{{outline:2px solid #1767a6;outline-offset:3px}}.visual-image-link{{display:block;width:max-content;cursor:zoom-in}}
.visual-scroll img{{display:block;max-width:none;height:auto}}figcaption{{display:flex;flex-wrap:wrap;gap:.3rem 1rem;margin-top:.5rem;font-size:.85rem;color:#516170}}
th,td{{border:1px solid #ccd5df;padding:.6rem .8rem;text-align:left;vertical-align:top;overflow-wrap:anywhere}}th{{background:#edf3f8}}
.table-scroll{{overflow-x:auto}}pre{{overflow-x:auto;padding:1rem;background:#f0f3f6}}code{{font-family:monospace}}
details{{margin:1rem 0;padding:1rem;border:1px solid #cdd8e3;border-radius:6px}}summary{{cursor:pointer;font-weight:600}}
.preview-note{{color:#516170;font-size:.9rem}}@media(max-width:600px){{main{{margin:0;padding:20px;border-radius:0}}body{{font-size:16px}}h1{{font-size:1.6rem}}}}
</style></head><body><main><h1>{escape(title)}</h1><p class="preview-note">승인용 로컬 미리보기 · 내용과 배치를 확인합니다. Notion의 실제 화면은 별도 검증합니다.</p>
{content}</main></body></html>"""
