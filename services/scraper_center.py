from __future__ import annotations

import csv
import inspect
import io
import json
from typing import Any

from services.providers import PROVIDERS, _normalise_video, get_client, get_spec


HOME_METHODS = {
    "hqporner": ("get_top_porn",),
    "bellesa": ("get_videos",),
    "pornhat": ("get_videos",),
    "youjizz": ("videos",),
}
CATEGORY_METHODS = ("get_category", "get_videos_by_category")
CATEGORY_LIST_METHODS = ("get_all_categories", "get_categories", "categories")
PROFILE_METHODS = (
    "get_playlist", "get_channel", "get_site", "get_model",
    "get_creator", "get_user", "get_pornstar", "get_profile",
)


def _unwrap(value: Any) -> Any:
    if hasattr(value, "unwrap") and callable(value.unwrap):
        try:
            return value.unwrap()
        except Exception:
            return value
    return value


async def _resolve(value: Any) -> Any:
    if inspect.isawaitable(value):
        value = await value
    return _unwrap(value)


async def _collect(value: Any, provider: str, limit: int) -> list[dict]:
    value = await _resolve(value)
    raw = []
    if hasattr(value, "__aiter__"):
        async for item in value:
            item = _unwrap(item)
            raw.append(item)
            if len(raw) >= limit:
                break
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)[:limit]
    elif value is not None:
        raw = [value]

    output, seen = [], set()
    for item in raw:
        mapped = _normalise_video(_unwrap(item), provider)
        if not mapped or mapped["url"] in seen:
            continue
        seen.add(mapped["url"])
        output.append(mapped)
        if len(output) >= limit:
            break
    return output


def _enum_value(module, enum_names: tuple[str, ...], members: tuple[str, ...], fallback: str):
    for enum_name in enum_names:
        enum_type = getattr(module, enum_name, None)
        if enum_type:
            for member in members:
                value = getattr(enum_type, member, None)
                if value is not None:
                    return value
    return fallback


def _listing_members(listing: str) -> tuple[str, ...]:
    if listing == "latest":
        return ("RECENT", "NEW", "LATEST", "RECENTLY_ADDED")
    if listing == "trending":
        return ("TRENDING", "HOT", "POPULAR", "MOST_VIEWED")
    return ("POPULAR", "MOST_VIEWED", "TOP", "TRENDING")


async def _call_pages(method, query: str | None, start_page: int, pages: int, limit: int):
    signature = inspect.signature(method)
    params = signature.parameters
    if start_page != 1 and "start_page" not in params:
        raise RuntimeError("This provider API does not support starting from a later page.")
    kwargs = {}
    if "pages" in params:
        kwargs["pages"] = pages
    if "start_page" in params:
        kwargs["start_page"] = start_page
    if "per_page" in params:
        kwargs["per_page"] = min(50, limit)
    if "limit" in params:
        kwargs["limit"] = limit

    if query is None:
        return await _resolve(method(**kwargs))
    positional = [
        p for p in params.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and p.name not in kwargs
    ]
    if positional:
        return await _resolve(method(query, **kwargs))
    for key in ("query", "url", "category", "name"):
        if key in params:
            kwargs[key] = query
            return await _resolve(method(**kwargs))
    return await _resolve(method(query, **kwargs))


def _method(client, names: tuple[str, ...]):
    for name in names:
        candidate = getattr(client, name, None)
        if callable(candidate):
            return name, candidate
    return None, None


async def scrape_search(provider: str, query: str, start_page: int, end_page: int, limit: int) -> list[dict]:
    client = get_client(provider)
    name, method = _method(client, ("search_videos", "search"))
    if not method:
        raise RuntimeError(f"{get_spec(provider).name} has no search method.")
    value = await _call_pages(method, query, start_page, end_page - start_page + 1, limit)
    return await _collect(value, provider, limit)


async def scrape_homepage(provider: str, listing: str, pages: int, limit: int) -> list[dict]:
    client = get_client(provider)
    module = __import__(get_spec(provider).module, fromlist=["*"])
    name, method = _method(client, HOME_METHODS.get(provider, ()))
    if not method:
        raise RuntimeError(
            f"{get_spec(provider).name} does not expose a known homepage/listing method. "
            "Use Bulk scrape or Categories instead."
        )
    params = inspect.signature(method).parameters
    kwargs = {}
    if "pages" in params:
        kwargs["pages"] = pages
    if "sort_by" in params:
        sort_values = {
            "latest": ("RECENT", "NEW", "LATEST", "DATE"),
            "popular": ("VIEWS", "POPULAR", "MOST_VIEWED", "TOP"),
            "trending": ("TRENDING", "HOT", "RATING", "POPULAR"),
        }
        kwargs["sort_by"] = _enum_value(
            module, ("SortBy", "Sort", "Sorting"), sort_values.get(listing, ("POPULAR",)), listing
        )
    if "sort" in params:
        kwargs["sort"] = _enum_value(
            module, ("Sort", "VideoSort", "Sorting"), _listing_members(listing), listing
        )
    if "listing" in params:
        kwargs["listing"] = _enum_value(
            module, ("Listing", "VideoListing"), _listing_members(listing), listing
        )
    value = await _resolve(method(**kwargs))
    return await _collect(value, provider, limit)


async def list_categories(provider: str) -> list[str]:
    client = get_client(provider)
    _, method = _method(client, CATEGORY_LIST_METHODS)
    if not method:
        raise RuntimeError(f"{get_spec(provider).name} does not expose a category-list method.")
    value = await _resolve(method())
    if hasattr(value, "__aiter__"):
        items = []
        async for item in value:
            items.append(_unwrap(item))
            if len(items) >= 200:
                break
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    elif isinstance(value, dict):
        items = list(value.values())
    else:
        items = [value]
    labels = []
    for item in items[:200]:
        if isinstance(item, str):
            label = item
        elif isinstance(item, dict):
            label = item.get("name") or item.get("title") or item.get("url") or item.get("slug")
        else:
            label = getattr(item, "name", None) or getattr(item, "title", None) or getattr(item, "url", None)
        if label:
            labels.append(str(label))
    return labels


async def scrape_category(provider: str, category: str, pages: int, limit: int) -> list[dict]:
    client = get_client(provider)
    _, method = _method(client, CATEGORY_METHODS)
    if not method:
        raise RuntimeError(f"{get_spec(provider).name} does not expose a known category scraper.")
    value = await _call_pages(method, category, 1, pages, limit)
    return await _collect(value, provider, limit)


async def scrape_profile(provider: str, profile_url: str, pages: int, limit: int) -> list[dict]:
    client = get_client(provider)
    name, method = _method(client, PROFILE_METHODS)
    if not method:
        raise RuntimeError(f"{get_spec(provider).name} does not expose a known profile/playlist method.")
    value = await _call_pages(method, profile_url, 1, pages, limit)
    return await _collect(value, provider, limit)


def export_json(items: list[dict]) -> bytes:
    return json.dumps(items, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def export_csv(items: list[dict]) -> bytes:
    fields = ("title", "url", "video_id", "provider", "creator", "duration", "views", "uploaded", "thumbnail")
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(items)
    return stream.getvalue().encode("utf-8-sig")
