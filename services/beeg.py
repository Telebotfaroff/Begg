from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession


BEEG_ORIGIN = "https://beeg.com"
VIDEO_PATH = re.compile(r"/(?:-[0-9]{5,}|video/-?[0-9]{5,})(?:/|$)")


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    video_id: str
    thumbnail: str | None = None


def clean_video_url(value: str) -> str | None:
    value = value.strip()
    if re.fullmatch(r"\d{5,}", value):
        return f"{BEEG_ORIGIN}/-0{value}"
    if not value.startswith(("https://", "http://")):
        return None
    if "beeg.com" not in value.lower():
        return None
    if VIDEO_PATH.search(value.split("?", 1)[0] + ("/" if not value.endswith("/") else "")):
        return value
    return None


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


async def search_videos(query: str, limit: int = 10) -> list[SearchResult]:
    """Search the site's HTML search route and normalize video cards.

    The Beeg metadata package currently exposes get_video(url), not keyword search;
    this adapter isolates the site's search-page markup from the downloader.
    """
    search_url = f"{BEEG_ORIGIN}/search?q={quote(query)}"
    async with AsyncSession(impersonate="chrome") as session:
        response = await session.get(search_url, timeout=30, allow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

    found: dict[str, SearchResult] = {}
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        absolute = urljoin(BEEG_ORIGIN, href)
        if "beeg.com" not in absolute.lower():
            continue
        path = absolute.split("?", 1)[0]
        match = re.search(r"/-0*(\d{5,})(?:/|$)", path)
        if not match:
            match = re.search(r"/video/-?0*(\d{5,})(?:/|$)", path)
        if not match:
            continue

        video_id = match.group(1).lstrip("0") or match.group(1)
        if video_id in found:
            continue
        title = anchor.get("title") or anchor.get_text(" ", strip=True)
        if not title:
            parent = anchor.find_parent()
            title = parent.get_text(" ", strip=True) if parent else ""
        title = re.sub(r"\s+", " ", title).strip() or f"Beeg video {video_id}"

        image = anchor.find("img")
        thumb = None
        if image:
            thumb = image.get("data-src") or image.get("src")
            if thumb:
                thumb = urljoin(BEEG_ORIGIN, thumb)

        found[video_id] = SearchResult(
            title=title[:240],
            url=f"{BEEG_ORIGIN}/-0{video_id}",
            video_id=video_id,
            thumbnail=thumb,
        )
        if len(found) >= limit:
            break
    return list(found.values())
