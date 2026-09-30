import base64
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from cs_study_mcp.assets import (
    AssetError,
    _inside,
    render_diagram,
    store_asset,
    validate_diagram_spec,
    verify_asset,
)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


def test_content_addressed_import_is_immutable_and_detects_tampering(tmp_path):
    source = tmp_path / "screenshot.png"
    source.write_bytes(PNG)
    metadata = store_asset(tmp_path, source)
    assert metadata["hash"] == hashlib.sha256(PNG).hexdigest()
    assert metadata["path"] == ".cs-study/assets/" + metadata["hash"] + ".png"
    assert verify_asset(tmp_path, metadata)
    assert store_asset(tmp_path, source) == metadata
    (tmp_path / metadata["path"]).write_bytes(PNG + b"changed")
    assert not verify_asset(tmp_path, metadata)
    with pytest.raises(AssetError, match="modified"):
        store_asset(tmp_path, source)


def test_concurrent_import_never_publishes_partial_bytes(tmp_path):
    source = tmp_path / "screenshot.png"
    source.write_bytes(PNG)
    with ThreadPoolExecutor(max_workers=4) as pool:
        metadata = list(pool.map(lambda _: store_asset(tmp_path, source), range(8)))
    assert all(value == metadata[0] for value in metadata)
    assert verify_asset(tmp_path, metadata[0])
    assert len(list((tmp_path / ".cs-study/assets").iterdir())) == 1


def test_import_rejects_outside_paths_and_svg_screenshots(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "private.png"
    outside.write_bytes(PNG)
    with pytest.raises(AssetError, match="inside"):
        store_asset(root, outside)
    svg = root / "diagram.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>기초</text></svg>', encoding="utf-8")
    with pytest.raises(AssetError, match="PNG or JPEG"):
        store_asset(root, svg)
    metadata = store_asset(root, svg, generated=True)
    assert verify_asset(root, metadata)
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', encoding="utf-8")
    with pytest.raises(AssetError, match="Active"):
        store_asset(root, svg, generated=True)


@pytest.mark.parametrize("spec", [
    '%%{init: {"securityLevel":"loose"}}%%\nflowchart LR\nA-->B',
    'flowchart LR\nclick A "https://example.org"',
    'flowchart LR\nA["<img src=x>"]',
    'flowchart LR\nA@{ img: "anything" }',
    "---\nconfig: x\n---\nflowchart LR\nA-->B",
    "flowchart LR\nA[https://example.org]",
])
def test_diagram_cannot_change_security_or_load_resources(spec):
    with pytest.raises(AssetError):
        validate_diagram_spec(spec)


def test_missing_runtime_is_clear_error(tmp_path):
    with pytest.raises(AssetError, match="npm ci"):
        render_diagram(tmp_path, "flowchart LR\nA[프로세스] --> B[스레드]")


def test_render_uses_safe_argv_and_binds_spec_and_runtime(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/render-diagram.mjs").touch()
    runtime = tmp_path / "node_modules/@mermaid-js/mermaid-cli"
    runtime.mkdir(parents=True)
    (runtime / "package.json").write_text("{}")
    monkeypatch.setattr("cs_study_mcp.assets.shutil.which", lambda _: "node-test")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        Path(args[-1]).write_bytes(PNG)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"browser": "pinned-test"}), stderr="")

    monkeypatch.setattr("cs_study_mcp.assets.subprocess.run", run)
    spec = "flowchart LR\nA[프로세스] --> B[스레드]"
    metadata = render_diagram(tmp_path, spec)
    assert metadata["spec_hash"] == hashlib.sha256(spec.encode()).hexdigest()
    assert len(metadata["renderer_fingerprint"]) == 64
    assert verify_asset(tmp_path, metadata)
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["timeout"] == 90
    assert len(calls[0][0]) == 4
    assert not list((tmp_path / ".cs-study/render-tmp").iterdir())


def test_metadata_path_cannot_escape_store(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    metadata = store_asset(tmp_path, source)
    metadata["path"] = "source.png"
    assert not verify_asset(tmp_path, metadata)


@pytest.mark.skipif(os.name != "nt", reason="Windows realpath extended-length prefix regression")
def test_windows_concurrent_resolution_prefix_is_same_filesystem_path(tmp_path, monkeypatch):
    original = Path.resolve

    def racing_resolve(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path.name == "candidate.png":
            return Path("\\\\?\\" + str(result))
        return result

    monkeypatch.setattr(Path, "resolve", racing_resolve)
    assert _inside(tmp_path, "candidate.png") == tmp_path / "candidate.png"
    with pytest.raises(AssetError, match="inside"):
        _inside(tmp_path, tmp_path.parent / "candidate.png")
