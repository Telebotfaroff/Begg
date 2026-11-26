from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

BEEG_ORIGIN = "https://beeg.com"
VIDEO_ID_RE = re.compile(r"/(?:-0*|video/-?0*)(\d{5,})(?:/|$)")


@dataclass(slots=True)
class CategoryVideo:
    video_id: str
    title: str
    url: str
    thumbnail: str | None = None
    duration: str | None = None
    creator: str | None = None
    creator_url: str | None = None
    views: str | None = None
    uploaded: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _text(node: Tag | None) -> str | None:
    if not node:
        return None
    value = " ".join(node.stripped_strings)
    return value or None


def _video_id(href: str | None) -> str | None:
    if not href:
        return None
    match = VIDEO_ID_RE.search(urlparse(href).path)
    return match.group(1) if match else None


def _absolute_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    return urljoin(base_url, value)


def parse_category_html(
    html: str,
    *,
    base_url: str = BEEG_ORIGIN,
    limit: int | None = None,
) -> list[CategoryVideo]:
    """Parse video cards already rendered in a Beeg category/search HTML page.

    The captured Beeg page uses data-testid='unit' cards. This parser deliberately
    extracts only rendered cards; it does not guess the site's infinite-scroll API.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('[data-testid="unit"]')
    results: list[CategoryVideo] = []
    seen: set[str] = set()

    for card in cards:
        info = card.select_one('[data-testid="unit-info"]')
        media = card.select_one('[data-testid="unit-media"]')
        link = (info or media or card).find("a", href=VIDEO_ID_RE)
        if not link:
            # Some page variants put the video link on the media anchor only.
            link = card.find("a", href=VIDEO_ID_RE)
        video_id = _video_id(link.get("href") if link else None)
        if not video_id or video_id in seen:
            continue

        title_node = card.select_one('[data-testid="unit-title"]')
        image = card.select_one('[data-testid="unit-media"] img') or card.select_one("img")
        duration_node = card.select_one('[data-testid="unit-amount"]')
        avatar = card.select_one('[data-testid="unit-avatar"]')
        creator_image = avatar.select_one("img") if avatar else None

        # Creator label is the first text block in the info section, before title.
        creator = None
        if info:
            text_blocks = info.select("div.tw-flex.tw-flex-col > div")
            if text_blocks:
                creator_node = text_blocks[0].find(string=True, recursive=False)
                creator = str(creator_node).strip() if creator_node else _text(text_blocks[0])

        stats: list[str] = []
        if info:
            stats_row = info.select_one("div.tw-flex.tw-mt-2")
            if stats_row:
                stats = [_text(child) or "" for child in stats_row.find_all("span", recursive=False)]
        views = stats[0] if stats else None
        uploaded = stats[1] if len(stats) > 1 else None

        href = link.get("href") if link else f"/-0{video_id}"
        results.append(
            CategoryVideo(
                video_id=video_id,
                title=_text(title_node) or (image.get("alt") if image else None) or f"Beeg video {video_id}",
                url=_absolute_url(href, base_url) or f"{BEEG_ORIGIN}/-0{video_id}",
                thumbnail=_absolute_url(image.get("src") or image.get("data-src"), base_url) if image else None,
                duration=_text(duration_node),
                creator=creator or (creator_image.get("alt") if creator_image else None),
                creator_url=_absolute_url(avatar.get("href"), base_url) if avatar and avatar.get("href") else None,
                views=views,
                uploaded=uploaded,
            )
        )
        seen.add(video_id)
        if limit is not None and len(results) >= max(0, limit):
            break

    return results
