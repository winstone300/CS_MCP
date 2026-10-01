import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID


def notion_id(value: str) -> str:
    """Accept an exact UUID or a canonical notion.so/notion.site page URL."""
    value = value.strip()
    if value.startswith(("http://", "https://")):
        url = urlparse(value)
        hostname = (url.hostname or "").lower()
        if url.scheme != "https" or not (
            hostname in {"notion.so", "www.notion.so", "notion.site", "app.notion.com"}
            or hostname.endswith(".notion.site")
        ):
            raise ValueError("Notion HTTPS 페이지 URL 또는 페이지 UUID를 입력하세요.")
        value = url.path.rstrip("/").split("/")[-1]
        match = re.search(r"([0-9a-fA-F]{32}|[0-9a-fA-F-]{36})$", value)
        if not match:
            raise ValueError("URL에서 Notion 페이지 ID를 찾을 수 없습니다.")
        value = match.group(1)
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError("올바른 Notion 페이지 UUID가 아닙니다.") from exc


def configured_parent(root: Path) -> str | None:
    path = root / "study.local.toml"
    if not path.exists():
        return None
    with path.open("rb") as file:
        config = tomllib.load(file)
    value = config.get("notion", {}).get("parent_page_id")
    return notion_id(value) if value else None
