"""Local immutable visual assets. No model calls, remote fetching, or shell commands."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

MAX_ASSET_BYTES = 20 * 1024 * 1024
RENDERER_VERSION = "mermaid-local-v1"


class AssetError(ValueError):
    """The asset cannot safely be stored or rendered."""


def _resolved(path: Path) -> Path:
    resolved = path.resolve()
    # On Windows, non-strict realpath can return an extended-length prefix when
    # another writer creates a previously absent ancestor during resolution.
    # Normalize only filesystem drive/UNC prefixes, after resolving symlinks.
    value = str(resolved)
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif re.match(r"^\\\\\?\\[A-Za-z]:\\", value):
            value = value[4:]
    return Path(value)


def _inside(root: Path, path: str | Path) -> Path:
    path = Path(path)
    resolved = _resolved(path if path.is_absolute() else root / path)
    if not resolved.is_relative_to(_resolved(root)):
        raise AssetError("Asset path must stay inside the project root")
    return resolved


def _image_type(data: bytes, *, allow_svg: bool) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        if data[12:16] != b"IHDR" or not int.from_bytes(data[16:20], "big") or not int.from_bytes(data[20:24], "big"):
            raise AssetError("Invalid PNG header")
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "image/jpeg", ".jpg"
    if allow_svg:
        _validate_svg(data)
        return "image/svg+xml", ".svg"
    raise AssetError("Screenshots must be PNG or JPEG; SVG is reserved for generated diagrams")


def _validate_svg(data: bytes) -> None:
    if re.search(br"<!\s*(DOCTYPE|ENTITY)", data, re.I):
        raise AssetError("SVG declarations are not allowed")
    try:
        element = ET.fromstring(data)
    except ET.ParseError as exc:
        raise AssetError("Invalid generated SVG") from exc
    if element.tag.rsplit("}", 1)[-1] != "svg":
        raise AssetError("Asset is not an SVG image")
    for node in element.iter():
        if node.tag.rsplit("}", 1)[-1].lower() in {"script", "foreignobject", "iframe", "image", "use", "a"}:
            raise AssetError("Active or externally referenced SVG content is not allowed")
        for attribute, value in node.attrib.items():
            attribute = attribute.rsplit("}", 1)[-1].lower()
            if attribute.startswith("on") or attribute in {"href", "src"}:
                raise AssetError("Active SVG attributes are not allowed")
            if re.search(r"(?:javascript:|https?://|@import|url\(\s*[^#])", value, re.I):
                raise AssetError("External SVG resources are not allowed")
        if node.tag.rsplit("}", 1)[-1] == "style" and node.text:
            if re.search(r"(?:@import|https?://|javascript:|url\(\s*[^#])", node.text, re.I):
                raise AssetError("External SVG styles are not allowed")


def store_asset(root: str | Path, path: str | Path, *, generated: bool = False) -> dict[str, Any]:
    """Import a project-local image into a content-addressed, write-once store.

    Only the renderer should set ``generated``. User screenshots cannot import SVG.
    """
    root = _resolved(Path(root))
    source = _inside(root, path)
    if not source.is_file() or source.stat().st_size > MAX_ASSET_BYTES:
        raise AssetError("Asset is missing or exceeds the 20 MiB limit")
    data = source.read_bytes()
    mime_type, extension = _image_type(data, allow_svg=generated)
    asset_hash = hashlib.sha256(data).hexdigest()
    destination = _inside(root, Path(".cs-study") / "assets" / (asset_hash + extension))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Publish a complete file via a hard link: unlike replace(), this cannot
    # overwrite an existing content-addressed object, even with concurrent writers.
    descriptor, temporary = tempfile.mkstemp(prefix=".import-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.read_bytes() != data:
                raise AssetError("Existing content-addressed asset has been modified") from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {
        "hash": asset_hash,
        "mime_type": mime_type,
        "size": len(data),
        "path": destination.relative_to(root).as_posix(),
    }


def verify_asset(root: str | Path, metadata: dict[str, Any]) -> bool:
    """Verify path, bytes, type, size and content-addressed name, never URL equality."""
    try:
        root = _resolved(Path(root))
        asset_hash = metadata.get("hash") or metadata.get("asset_hash")
        if not isinstance(asset_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", asset_hash):
            return False
        path = _inside(root, metadata["path"])
        if path.parent != _inside(root, ".cs-study/assets"):
            return False
        if not path.is_file() or path.stat().st_size > MAX_ASSET_BYTES:
            return False
        data = path.read_bytes()
        mime_type, extension = _image_type(data, allow_svg=True)
        return (
            hashlib.sha256(data).hexdigest() == asset_hash
            and path.name == asset_hash + extension
            and metadata["mime_type"] == mime_type
            and metadata["size"] == len(data)
        )
    except (AssetError, KeyError, OSError, TypeError):
        return False


def validate_diagram_spec(spec: str) -> None:
    """Reject embedded config, HTML and resource URLs before invoking Mermaid."""
    if not spec.strip() or len(spec.encode("utf-8")) > 100_000:
        raise AssetError("Diagram must contain 1–100000 UTF-8 bytes")
    forbidden = [
        r"%%\s*\{", r"^\s*---", r"\b(?:https?|file|data|javascript)\s*:",
        r"<\s*/?\s*[a-zA-Z][^>]*>", r"^\s*click\b", r"\b(?:img|icon)\s*:",
        r"@import", r"url\s*\(",
    ]
    if any(re.search(pattern, spec, re.I | re.M) for pattern in forbidden):
        raise AssetError("Diagram cannot contain directives, HTML, actions or external resources")
    diagram_type = spec.lstrip().split(maxsplit=1)[0]
    if diagram_type not in {"flowchart", "graph", "sequenceDiagram", "timeline", "stateDiagram-v2", "classDiagram", "erDiagram"}:
        raise AssetError("Unsupported Mermaid diagram type")


def render_diagram(root: str | Path, spec: str, kind: str = "structure") -> dict[str, Any]:
    """Render a reviewed definition using the project's pinned, offline runtime."""
    root = _resolved(Path(root))
    if kind not in {"structure", "flow", "timeline"}:
        raise AssetError("Diagram kind must be structure, flow or timeline")
    validate_diagram_spec(spec)
    node = shutil.which("node")
    script = root / "scripts" / "render-diagram.mjs"
    if not node or not script.is_file() or not (root / "node_modules/@mermaid-js/mermaid-cli/package.json").is_file():
        raise AssetError("Pinned Mermaid runtime is unavailable; install with npm ci and configure a local Chromium browser")
    work = _inside(root, ".cs-study/render-tmp")
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="diagram-", dir=work) as directory:
        input_path = Path(directory) / "input.mmd"
        output_path = Path(directory) / "output.png"
        input_path.write_text(spec, encoding="utf-8", newline="\n")
        try:
            result = subprocess.run(
                [node, str(script), str(input_path), str(output_path)],
                cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=90, check=False, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AssetError(f"Diagram rendering failed: {type(exc).__name__}") from exc
        if result.returncode or not output_path.is_file():
            raise AssetError("Diagram rendering failed: " + (result.stderr or result.stdout)[-2000:])
        try:
            runtime = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise AssetError("Renderer did not report its runtime fingerprint") from exc
        metadata = store_asset(root, output_path, generated=True)
        metadata.update(
            spec_hash=hashlib.sha256(spec.encode("utf-8")).hexdigest(),
            renderer_fingerprint=hashlib.sha256(json.dumps(
                {"version": RENDERER_VERSION, "runtime": runtime}, sort_keys=True,
            ).encode()).hexdigest(),
            renderer_runtime=runtime,
        )
        return metadata
