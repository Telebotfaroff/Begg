from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

import websockets
from curl_cffi.requests import AsyncSession

BEEG_ORIGIN = "https://beeg.com"
STORE_API = "https://store.externulls.com"
SEARCH_WS = "wss://search.externulls.com"
THUMB_API = "https://thumbs.externulls.com"
VIDEO_PATH = re.compile(r"/(?:-0*([0-9]{5,})|video/-?0*([0-9]{5,}))(?:/|$)")


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    video_id: str
    thumbnail: str | None = None


def clean_video_url(value: str) -> str | None:
    value = value.strip()
    if re.fullmatch(r"\d{5,}", value):
        return f"{BEEG_ORIGIN}/-0{value.lstrip('0') or value}"
    if not value.startswith(("https://", "http://")) or "beeg.com" not in value.lower():
        return None
    match = VIDEO_PATH.search(value.split("?", 1)[0] + ("/" if not value.endswith("/") else ""))
    if not match:
        return None
    video_id = next((part for part in match.groups() if part), None)
    return f"{BEEG_ORIGIN}/-0{video_id}" if video_id else None


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _first_value(obj: dict, *keys: str):
    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return value
    return None


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _extract_records(payload) -> list[dict]:
    records, seen = [], set()
    for item in _walk_dicts(payload):
        file_data = item.get("file") if isinstance(item.get("file"), dict) else {}
        unit = item.get("unit") if isinstance(item.get("unit"), dict) else {}
        unit_file = unit.get("file") if isinstance(unit.get("file"), dict) else {}
        file_id = (
            _first_value(file_data, "id", "file_id")
            or _first_value(unit_file, "id", "file_id")
            or _first_value(item, "file_id", "fileId")
        )
        if not file_id:
            continue
        fact_id = _first_value(item, "id", "fact_id")
        key = (str(file_id), str(fact_id or ""))
        if key not in seen:
            seen.add(key)
            records.append(item)
    return records


def _normalise_record(record: dict) -> SearchResult | None:
    file_data = record.get("file") if isinstance(record.get("file"), dict) else {}
    unit = record.get("unit") if isinstance(record.get("unit"), dict) else {}
    # Search responses may contain a wrapper with "unit" or the unit itself.
    if not unit and isinstance(record.get("file"), dict):
        unit = record
    if not file_data and isinstance(unit.get("file"), dict):
        file_data = unit["file"]
    file_id = (
        _first_value(file_data, "id", "file_id")
        or _first_value(unit.get("file", {}) if isinstance(unit.get("file"), dict) else {}, "id", "file_id")
        or _first_value(record, "file_id", "fileId")
    )
    if not file_id or not str(file_id).isdigit():
        return None
    video_id = str(file_id)
    fact = record.get("fact") if isinstance(record.get("fact"), dict) else {}
    facts = unit.get("fc_facts") if isinstance(unit.get("fc_facts"), list) else []
    latest_fact = facts[-1] if facts and isinstance(facts[-1], dict) else {}
    title = (
        _first_value(record, "title", "video_title", "name")
        or _first_value(unit, "title", "video_title", "name")
        or _first_value(fact, "title", "name")
        or _first_value(latest_fact, "title", "name")
        or f"Beeg video {video_id}"
    )
    thumbs = (
        record.get("thumbs") or record.get("fc_thumbs")
        or unit.get("thumbs") or latest_fact.get("fc_thumbs") or []
    )
    thumb_id = None
    if isinstance(thumbs, list) and thumbs:
        first_thumb = thumbs[0]
        if isinstance(first_thumb, dict):
            thumb_id = _first_value(first_thumb, "id", "offset", "fc_thumb")
        elif isinstance(first_thumb, (str, int)):
            thumb_id = first_thumb
    offset = str(thumb_id if thumb_id is not None else 0)
    return SearchResult(
        title=re.sub(r"\s+", " ", str(title)).strip()[:240],
        url=f"{BEEG_ORIGIN}/-0{video_id}",
        video_id=video_id,
        thumbnail=f"{THUMB_API}/videos/{video_id}/{offset}.webp",
    )


async def _resolve_category_id(session: AsyncSession, slug: str) -> int | None:
    try:
        response = await session.get(f"{STORE_API}/tag", params={"slug": slug}, timeout=15)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None
    if isinstance(data, dict):
        candidates = data.get("data") or data.get("tags") or data.get("items") or [data]
    elif isinstance(data, list):
        candidates = data
    else:
        candidates = []
    for item in candidates:
        if isinstance(item, dict) and str(item.get("tg_slug", item.get("slug", ""))).lower() == slug.lower():
            tag_id = item.get("id")
            return int(tag_id) if str(tag_id).isdigit() else None
    return None


async def _websocket_search(query: str, limit: int, category_id: int | None = None, offset: int = 0):
    request_type = "search_category" if category_id else "search"
    request = {
        "type": request_type,
        "ignore_stats": True,
        "payload": {
            "Search_string": query,
            "offset": offset,
            "limit": limit,
            "category_id": category_id,
        },
    }
    async with websockets.connect(
        SEARCH_WS, origin=BEEG_ORIGIN, open_timeout=15, close_timeout=5,
        ping_interval=20, ping_timeout=20, max_size=8 * 1024 * 1024,
    ) as socket:
        await socket.send(json.dumps(request))
        while True:
            raw = await asyncio.wait_for(socket.recv(), timeout=25)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                if payload.get("type") in {"error", "search_error"}:
                    raise RuntimeError(str(payload)[:500])
                if _extract_records(payload) or any(k in payload for k in ("results", "items", "data", "units", "facts")):
                    return payload
            elif isinstance(payload, list):
                return payload


async def search_videos(query: str, limit: int = 10) -> list[SearchResult]:
    """Search using Beeg's frontend WebSocket protocol instead of scraping HTML."""
    term = query.strip().strip("/")
    if not term:
        return []
    # This entry point is keyword search. Category browsing is a separate
    # frontend operation; guessing a category from a one-word query can turn
    # ordinary searches into invalid search_category requests.
    payload = await _websocket_search(term, max(1, min(int(limit), 50)))
    results, seen = [], set()
    for record in _extract_records(payload):
        item = _normalise_record(record)
        if item and item.video_id not in seen:
            seen.add(item.video_id)
            results.append(item)
        if len(results) >= limit:
            break
    return results
