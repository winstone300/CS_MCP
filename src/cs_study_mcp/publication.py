"""Conservative, loss-aware publication readback validation for readable v2.

The accepted subset is shared by the renderer and the writer. Unsupported Notion
representations fail closed instead of silently removing unrecognized content.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from copy import deepcopy
from html.parser import HTMLParser
from typing import Any


class PublicationFormatError(ValueError):
    """A readback contains malformed or unsupported publication syntax."""


class _NotionTableParser(HTMLParser):
    """The documented Notion table subset; rich cells cannot contain blocks.

    Color and other unsupported attributes fail closed. They are never discarded
    to make a changed table match the approved, unstyled document.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.flags = {"header-row": False, "header-column": False, "fit-page-width": False}
        self.rows: list[list[str]] = []
        self.cell: list[str] = []
        self.columns = 0
        self.colgroup_seen = False
        self.started = False
        self.finished = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent = self.stack[-1] if self.stack else None
        if len({name for name, _ in attrs}) != len(attrs):
            raise PublicationFormatError("Duplicate Notion table attributes")
        if tag == "table" and parent is None and not self.started:
            for name, value in attrs:
                if name not in self.flags or value not in {"true", "false"}:
                    raise PublicationFormatError("Unsupported Notion table attribute or value")
                self.flags[name] = value == "true"
            self.started = True
        elif attrs:
            raise PublicationFormatError("Styled or unknown Notion table content is unsupported")
        elif tag == "colgroup" and parent == "table" and not self.rows and not self.colgroup_seen:
            self.colgroup_seen = True
        elif tag == "col" and parent == "colgroup":
            self.columns += 1
            return  # Documented Notion col is a void element.
        elif tag == "tr" and parent == "table":
            self.rows.append([])
        elif tag == "td" and parent == "tr":
            self.cell = []
        elif tag == "br" and parent == "td":
            self.cell.append("<br>")
            return
        else:
            raise PublicationFormatError("Unsupported or misplaced Notion table element")
        self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"col", "br"}:
            raise PublicationFormatError("Only col and br may be self-closing in a Notion table")
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            raise PublicationFormatError("Mismatched Notion table tags")
        if tag == "td":
            self.rows[-1].append("".join(self.cell).strip())
        elif tag == "tr" and not self.rows[-1]:
            raise PublicationFormatError("A Notion table row must contain cells")
        elif tag == "table":
            self.finished = True
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1] == "td":
            self.cell.append(data)
        elif data.strip():
            raise PublicationFormatError("Content outside Notion table cells is unsupported")

    def handle_entityref(self, name: str) -> None:
        self.handle_data(html.unescape("&" + name + ";"))

    def handle_charref(self, name: str) -> None:
        self.handle_data(html.unescape("&#" + name + ";"))

    def handle_comment(self, data: str) -> None:
        raise PublicationFormatError("Comments in Notion tables are unsupported")

    def handle_decl(self, decl: str) -> None:
        raise PublicationFormatError("Declarations in Notion tables are unsupported")

    def handle_pi(self, data: str) -> None:
        raise PublicationFormatError("Instructions in Notion tables are unsupported")

    def unknown_decl(self, data: str) -> None:
        raise PublicationFormatError("Unknown declarations in Notion tables are unsupported")

    def block(self) -> dict[str, Any]:
        if not self.finished or self.stack or not self.rows:
            raise PublicationFormatError("Unclosed or empty Notion table")
        width = len(self.rows[0])
        if any(len(row) != width for row in self.rows) or self.columns not in {0, width}:
            raise PublicationFormatError("Notion table row or column widths differ")
        header = self.flags["header-row"]
        return {
            "type": "table", "columns": self.rows[0] if header else [],
            "rows": self.rows[1:] if header else self.rows,
            "align": [None] * width, "header_row": header,
            "header_column": self.flags["header-column"],
            "fit_page_width": self.flags["fit-page-width"],
        }


def _notion_table(lines: list[str], start: int) -> tuple[dict[str, Any], int]:
    index = start
    while index < len(lines) and "</table>" not in lines[index]:
        index += 1
    if index == len(lines):
        raise PublicationFormatError("Unclosed Notion table")
    parser = _NotionTableParser()
    parser.feed("\n".join(lines[start:index + 1]))
    parser.close()
    return parser.block(), index + 1


def _table_cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line:
        if escaped:
            current.extend(["\\", character])
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def _alignment(line: str) -> list[str | None] | None:
    if "|" not in line:
        return None
    cells = _table_cells(line)
    if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
        return None
    return ["center" if cell.startswith(":") and cell.endswith(":") else "left" if cell.startswith(":") else "right" if cell.endswith(":") else None for cell in cells]


def canonicalize_markdown(text: str) -> list[dict[str, Any]]:
    """Parse the supported Markdown subset, preserving all meaningful blocks."""
    lines = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks, index = _parse(lines, 0, nested=False)
    if index != len(lines):
        raise PublicationFormatError("Unexpected content after toggle")
    return blocks


def _unindent_toggle_children(lines: list[str]) -> list[str]:
    """Remove a toggle tab while respecting Notion's raw code/table payloads."""
    result, index = [], 0
    while index < len(lines):
        line = lines[index]
        if line.strip() and not line.startswith("\t"):
            raise PublicationFormatError("Notion toggle children must all use one structural tab")
        result.append(line[1:] if line.startswith("\t") else line)
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        index += 1
        if marker:
            fence = marker.group(1)
            prefix = line[:len(line) - len(line.lstrip())]
            start = index
            while index < len(lines) and not re.fullmatch(
                r"\s*" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*",
                lines[index],
            ):
                index += 1
            payload = lines[start:index]
            # The renderer indents payloads with the opening fence; Notion
            # readback emits payloads at the root while keeping fences nested.
            structural = all(not value.strip() or value.startswith(prefix) for value in payload)
            result.extend(
                value[1:] if structural and value.startswith("\t") else value
                for value in payload
            )
            if index < len(lines):
                closing = lines[index]
                if not closing.startswith("\t"):
                    raise PublicationFormatError(
                        "Notion toggle children must all use one structural tab"
                    )
                result.append(closing[1:])
                index += 1
        elif re.match(r"^\s*<table(?:\s|>)", line):
            while index < len(lines) and lines[index].strip() != "</table>":
                # XML table internals carry no Markdown list/code indentation.
                result.append(lines[index])
                index += 1
    return result


def _toggle_children(lines: list[str], start: int) -> tuple[list[dict[str, Any]], int]:
    """Extract one toggle while preserving nested Markdown indentation.

    Enhanced Notion Markdown indents every child by one tab. Remove exactly
    that structural tab, leaving list depth and fenced-code whitespace intact.
    """
    depth = 1
    fence: str | None = None
    index = start
    while index < len(lines):
        stripped = lines[index].strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
        elif fence is None:
            if stripped == "<details>":
                depth += 1
            elif stripped == "</details>":
                depth -= 1
                if not depth:
                    children = lines[start:index]
                    first = next((line for line in children if line.strip()), "")
                    if first.startswith("\t"):
                        children = _unindent_toggle_children(children)
                    return canonicalize_markdown("\n".join(children)), index + 1
        index += 1
    raise PublicationFormatError("Unclosed toggle")


def _parse(lines: list[str], index: int, *, nested: bool) -> tuple[list[dict[str, Any]], int]:
    result: list[dict[str, Any]] = []
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped or stripped == "<empty-block/>":
            index += 1
            continue
        if re.match(r"<table(?:\s|>)", stripped):
            table, index = _notion_table(lines, index)
            result.append(table)
            continue
        if stripped == "</details>":
            if not nested:
                raise PublicationFormatError("Closing toggle without opening toggle")
            return result, index + 1
        if stripped == "<details>":
            index += 1
            while index < len(lines) and not lines[index].strip():
                index += 1
            summary = re.fullmatch(r"\s*<summary>(.*?)</summary>\s*", lines[index]) if index < len(lines) else None
            if not summary:
                raise PublicationFormatError("Toggle must have a single-line summary")
            children, index = _toggle_children(lines, index + 1)
            result.append({"type": "toggle", "title": summary.group(1), "children": children})
            continue
        # Notion reads a `text` code block back with its language name `plain text`.
        # Accept only that known multiword alias; preserve code and other languages.
        fence = re.fullmatch(r"\s*(`{3,}|~{3,})((?i:plain text)|[^\s`]*)\s*", line)
        if fence:
            marker, language = fence.groups()
            content = []
            index += 1
            while index < len(lines) and not re.fullmatch(r"\s*" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*", lines[index]):
                content.append(lines[index].rstrip())
                index += 1
            if index == len(lines):
                raise PublicationFormatError("Unclosed code fence")
            language = language.lower()
            if language == "plain text":
                language = "text"
            result.append({"type": "code", "language": language, "text": "\n".join(content)})
            index += 1
            continue
        alignment = _alignment(lines[index + 1]) if index + 1 < len(lines) else None
        if "|" in line and alignment is not None:
            columns = _table_cells(line)
            if len(columns) != len(alignment):
                raise PublicationFormatError("Table separator and header widths differ")
            rows = []
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                row = _table_cells(lines[index])
                if len(row) != len(columns):
                    raise PublicationFormatError("Table row width differs from its header")
                rows.append(row)
                index += 1
            result.append({"type": "table", "columns": columns, "rows": rows, "align": alignment,
                           "header_row": True, "header_column": False, "fit_page_width": False})
            continue
        image = re.fullmatch(r'!\[((?:\\.|[^\]\\])*)\]\((?:<([^>]+)>|([^\s]+?))(?:\s+"((?:\\.|[^"\\])*)")?\)', stripped)
        if image:
            result.append({"type": "image", "alt": image.group(1), "ref": image.group(2) or image.group(3), "title": image.group(4)})
            index += 1
            continue
        if "![" in line:
            raise PublicationFormatError("Images must use one complete image block per line")
        heading = re.fullmatch(r"(#{1,6})\s+(.+?)(?:\s+#+)?", stripped)
        if heading:
            result.append({"type": "heading", "level": len(heading.group(1)), "text": heading.group(2)})
            index += 1
            continue
        if re.fullmatch(r"(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,}", stripped):
            result.append({"type": "rule"})
            index += 1
            continue
        list_item = re.match(r"^(\s*)([-+*]|\d+[.)])\s+(.+)$", line)
        if list_item:
            indent, marker, item_text = list_item.groups()
            ordered = marker[0].isdigit()
            result.append({"type": "list_item", "ordered": ordered, "marker": int(marker[:-1]) if ordered else None, "indent": len(indent.expandtabs(4)), "text": item_text})
            index += 1
            continue
        if stripped.startswith(">"):
            quoted = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quoted.append(re.sub(r"^\s*> ?", "", lines[index]))
                index += 1
            result.append({"type": "quote", "children": canonicalize_markdown("\n".join(quoted))})
            continue
        # Deliberately reject HTML/Notion XML blocks, arbitrary directives and
        # indented code rather than flattening them into matching plain text.
        if re.search(r"<!--|<!DOCTYPE|<\??/?[A-Za-z][\w-]*(?:\s|/?>)", line, re.I) or stripped.startswith(":::"):
            raise PublicationFormatError("Unsupported HTML or Notion block syntax")
        if line.startswith(("    ", "\t")):
            raise PublicationFormatError("Use fenced code; indented content is ambiguous")
        # Each nonblank plain line is kept; no semantic reflow or text deletion.
        result.append({"type": "paragraph", "text": line})
        index += 1
    if nested:
        raise PublicationFormatError("Unclosed toggle")
    return result, index


def _walk(blocks: list[dict[str, Any]]):
    for block in blocks:
        yield block
        if "children" in block:
            yield from _walk(block["children"])


def _asset_hash(asset: dict[str, Any]) -> str | None:
    value = asset.get("hash") or asset.get("asset_hash")
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) else None


def _refs(asset: dict[str, Any]) -> set[str]:
    return {str(asset[key]) for key in ("ref", "remote_url", "observed_ref", "path") if asset.get(key)}


def _related_observations(ref: str, observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect all observations connected by a URL or stable upload reference.

    A later receipt is additional evidence, not permission to discard a byte
    observation from an earlier signed URL for the same uploaded object.
    """
    refs, remote_refs = {ref}, set()
    pending = list(observations)
    related = []
    while pending:
        remainder = []
        for observation in pending:
            source_refs = _refs(observation)
            remote_ref = str(observation["remote_ref"]) if observation.get("remote_ref") else None
            if refs.intersection(source_refs) or remote_ref in remote_refs:
                related.append(observation)
                refs.update(source_refs)
                if remote_ref:
                    remote_refs.add(remote_ref)
            else:
                remainder.append(observation)
        if len(remainder) == len(pending):
            break
        pending = remainder
    return related


def verify_readback(
    expected_blocks: list[dict[str, Any]],
    observed_markdown: str,
    observed_blocks: list[dict[str, Any]] | None = None,
    expected_assets: list[dict[str, Any]] | None = None,
    observed_assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare a real Markdown readback and verified image hash/receipt evidence.

    ``observed_assets`` hashes must come from readback bytes. Alternatively, its
    stable ``remote_ref`` must match the persisted upload receipt in expected
    assets. Merely returning the approved URL or hash in an image URL is not proof.
    """
    errors: list[str] = []
    try:
        actual = canonicalize_markdown(observed_markdown)
    except PublicationFormatError as exc:
        return {"valid": False, "errors": [str(exc)], "observed_hash": None}
    if observed_blocks is not None and observed_blocks != actual:
        errors.append("Supplied observed blocks disagree with the actual Markdown readback")
    expected = deepcopy(expected_blocks)
    expected_assets = expected_assets or []
    observed_assets = observed_assets or []
    approved: dict[str, dict[str, set[str]]] = {}
    for asset in expected_assets:
        if value := _asset_hash(asset):
            merged = approved.setdefault(value, {"refs": set(), "remote_refs": set()})
            merged["refs"].update(_refs(asset))
            if asset.get("remote_ref"):
                merged["remote_refs"].add(str(asset["remote_ref"]))
    expected_images = [block for block in _walk(expected) if block["type"] == "image"]
    actual_images = [block for block in _walk(actual) if block["type"] == "image"]
    for image in expected_images:
        ref = image["ref"]
        candidates = ({ref.removeprefix("asset://")} if ref.startswith("asset://") else
                      {key for key, asset in approved.items() if ref in asset["refs"]})
        if len(candidates) != 1 or not candidates.issubset(approved):
            errors.append("Expected image has missing or ambiguous approved asset metadata: " + ref)
        else:
            image["ref"] = "asset://" + next(iter(candidates))
    for image in actual_images:
        byte_hashes: set[str] = set()
        receipt_hashes: set[str] = set()
        malformed_hash = False
        for evidence in _related_observations(image["ref"], observed_assets):
            for key in ("hash", "asset_hash"):
                value = evidence.get(key)
                if value is not None:
                    if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
                        byte_hashes.add(value)
                    else:
                        malformed_hash = True
            if evidence.get("remote_ref"):
                remote_ref = str(evidence["remote_ref"])
                receipt_hashes.update(key for key, asset in approved.items() if remote_ref in asset["remote_refs"])
        matches = byte_hashes | receipt_hashes
        if (malformed_hash or len(byte_hashes) > 1 or not byte_hashes.issubset(approved)
                or (byte_hashes and receipt_hashes and byte_hashes != receipt_hashes)):
            errors.append("Image has conflicting or invalid observed byte-hash/receipt evidence: " + image["ref"])
        elif len(matches) != 1:
            errors.append("Image has no unambiguous byte-hash or stable upload-receipt proof: " + image["ref"])
        else:
            image["ref"] = "asset://" + next(iter(matches))
    if actual != expected:
        errors.append("Readback block order, text, table, toggle, diagram or image content differs")
    observed_hash = hashlib.sha256(json.dumps(actual, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"valid": not errors, "errors": errors, "observed_hash": observed_hash}
