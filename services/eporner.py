from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from eporner_api import Client, Encoding


@dataclass(slots=True)
class EpornerResult:
    title: str
    url: str
    video_id: str
    thumbnail: str | None = None
    creator: str | None = None
    duration: str | None = None
    views: str | int | None = None
    uploaded: str | None = None


client = Client()


def clean_video_url(value: str) -> str | None:
    value = value.strip()
    if not value.startswith(("https://", "http://")):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (parsed.hostname or "").lower() not in {"eporner.com", "www.eporner.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    video_id = None
    for prefix in ("video-", "hd-porn", "embed", "porno-video"):
        for index, part in enumerate(parts):
            if part.startswith(prefix + "-"):
                video_id = part[len(prefix) + 1:]
                break
            if part == prefix and index + 1 < len(parts):
                video_id = parts[index + 1]
                break
        if video_id:
            break
    if not video_id or not video_id.isalnum():
        return None
    return f"https://www.eporner.com/video-{video_id}/"


def is_category_url(value: str) -> bool:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    if (parsed.hostname or "").lower() not in {"eporner.com", "www.eporner.com"}:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) >= 2 and parts[0].lower() == "cat"


def _duration(video) -> str | None:
    value = getattr(video, "length_minutes", None)
    if value:
        return str(value)
    seconds = getattr(video, "length_seconds", None)
    if seconds:
        seconds = int(seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
    return None


def _normalise(video) -> EpornerResult | None:
    url = getattr(video, "url", None)
    video_id = getattr(video, "video_id", None)
    if not url or not video_id:
        return None
    performers = getattr(video, "performers", None) or []
    creator = getattr(video, "uploader", None)
    if not creator and performers and isinstance(performers[0], dict):
        creator = performers[0].get("name")
    return EpornerResult(
        title=str(getattr(video, "title", None) or f"Eporner video {video_id}"),
        url=str(url),
        video_id=str(video_id),
        thumbnail=getattr(video, "thumbnail", None),
        creator=str(creator) if creator else None,
        duration=_duration(video),
        views=getattr(video, "views", None),
        uploaded=getattr(video, "publish_date", None),
    )


async def _collect(stream, limit: int) -> list[EpornerResult]:
    results: list[EpornerResult] = []
    seen: set[str] = set()
    async for item in stream:
        video = item.unwrap()
        result = _normalise(video)
        if result and result.video_id not in seen:
            seen.add(result.video_id)
            results.append(result)
        if len(results) >= limit:
            break
    return results


async def search_videos(query: str, limit: int = 10) -> list[EpornerResult]:
    term = query.strip()
    if not term:
        return []
    limit = max(1, min(int(limit), 50))
    stream = client.search_videos(term, per_page=limit, pages=1)
    return await _collect(stream, limit)


async def fetch_category_videos(url: str, limit: int = 10) -> list[EpornerResult]:
    if not is_category_url(url):
        raise ValueError("Please provide an ePorner category URL.")
    parts = [part for part in urlsplit(url).path.split("/") if part]
    category = parts[1]
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    limit = max(1, min(int(limit), 50))
    stream = client.get_videos_by_category(category=category, pages=1, start_page=page)
    return await _collect(stream, limit)


async def get_video(url: str):
    canonical = clean_video_url(url)
    if not canonical:
        raise ValueError("Invalid ePorner video URL.")
    return await client.get_video(canonical, load_html=True, load_api=True)


__all__ = [
    "Encoding",
    "EpornerResult",
    "clean_video_url",
    "fetch_category_videos",
    "get_video",
    "is_category_url",
    "search_videos",
]
