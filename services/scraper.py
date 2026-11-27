from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from services.providers import PROVIDERS, get_client, get_spec, _normalise_video


PAGE_CAP = 20
RESULT_CAP = 500

# Provider-specific entry points. Missing methods are reported as unsupported;
# the adapter never guesses a method on a provider that does not expose it.
METHODS = {
    "eporner": {
        "homepage": ("get_top_videos", "get_latest_videos", "get_videos"),
        "categories": ("get_all_categories", "get_categories"),
        "category": ("get_videos_by_category", "get_category"),
        "profile": ("get_videos_by_user", "get_user_videos", "get_profile"),
    },
    "xvideos": {"homepage": ("get_trending_videos", "get_popular_videos", "get_videos"),
                "category": ("get_videos_by_category", "get_category"),
                "profile": ("get_channel", "get_playlist", "get_creator_videos")},
    "xnxx": {"homepage": ("get_trending_videos", "get_popular_videos", "get_videos"),
             "category": ("get_videos_by_category", "get_category")},
    "xhamster": {"homepage": ("get_trending_videos", "get_popular_videos", "get_videos"),
                 "category": ("get_videos_by_category", "get_category"),
                 "profile": ("get_creator_videos", "get_channel", "get_user_videos")},
    "youporn": {"homepage": ("get_trending_videos", "get_popular_videos", "get_videos"),
                "category": ("get_videos_by_category", "get_category"),
                "profile": ("get_channel", "get_collection", "get_user_videos")},
    "xfreehd": {"homepage": ("get_videos", "get_latest_videos"),
                "category": ("get_videos_by_category", "get_category"),
                "profile": ("get_album", "get_site")},
    "hqporner": {"homepage": ("get_top_porn",), "categories": ("get_all_categories",),
                 "category": ("get_videos_by_category",), "profile": ("get_videos_by_actress",)},
    "redtube": {"homepage": ("get_trending_videos", "get_popular_videos", "get_videos"),
                "category": ("get_videos_by_category", "get_category"),
                "profile": ("get_channel", "get_playlist", "get_user_videos")},
    "tube8": {"homepage": ("get_trending_videos", "get_popular_videos", "get_videos"),
              "category": ("get_videos_by_category", "get_category"),
              "profile": ("get_channel", "get_user_videos", "get_pornstar")},
    "thumbzilla": {"homepage": ("get_trending_videos", "get_popular_videos", "get_videos"),
                   "category": ("get_videos_by_category", "get_category"),
                   "profile": ("get_channel", "get_playlist", "get_user_videos")},
    "tnaflix": {"homepage": ("get_trending_videos", "get_popular_videos", "get_videos"),
                "category": ("get_videos_by_category", "get_category"),
                "profile": ("get_channel", "get_profile", "get_pornstar")},
    "bellesa": {"homepage": ("get_videos",), "categories": ("get_categories",),
                "category": ("get_category",), "profile": ("get_channel", "get_performer")},
    "empflix": {"homepage": ("get_trending_videos", "get_popular_videos", "get_videos"),
                "category": ("get_videos_by_category", "get_category"),
                "profile": ("get_channel", "get_profile", "get_pornstar")},
    "pornhat": {"homepage": ("get_videos",), "categories": ("get_categories",),
                "category": ("get_category",), "profile": ("get_site", "get_models", "get_channels")},
    "youjizz": {"homepage": ("videos",), "categories": ("categories",),
                "category": ("get_category",), "profile": ("pornstars",)},
}


@dataclass
class ScrapeOutcome:
    items: list[dict]
    method: str
    pages_requested: int
    truncated: bool = False


def _method(client: Any, provider: str, mode: str):
    candidates = METHODS.get(provider, {}).get(mode, ())
    for name in candidates:
        value = getattr(client, name, None)
        if callable(value):
            return name, value
    return None, None


def _kwargs_for(method, pages: int, start_page: int = 1) -> dict:
    try:
        params = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return {"pages": pages}
    values = {
        "pages": pages, "page": start_page, "start_page": start_page,
        "per_page": 30, "limit": RESULT_CAP, "sort": "new",
        "sort_by": "popular",
    }
    return {name: value for name, value in values.items() if name in params}


async def _resolve(value):
    if inspect.isawaitable(value):
        value = await value
    if hasattr(value, "unwrap") and callable(value.unwrap):
        value = value.unwrap()
    return value


async def _items(value, provider: str, limit: int) -> list[dict]:
    value = await _resolve(value)
    raw = []
    if hasattr(value, "__aiter__"):
        async for entry in value:
            entry = await _resolve(entry)
            raw.append(entry)
            if len(raw) >= limit:
                break
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)[:limit]
    elif value is not None:
        raw = [value]

    output, seen = [], set()
    for entry in raw:
        mapped = _normalise_video(entry, provider)
        if mapped and mapped["url"] not in seen:
            seen.add(mapped["url"])
            output.append(mapped)
            if len(output) >= limit:
                break
    return output


async def scrape(provider: str, mode: str, *, query: str = "", pages: int = 1,
                 limit: int = 100, start_page: int = 1) -> ScrapeOutcome:
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider.")
    if mode not in {"search", "homepage", "category", "profile"}:
        raise ValueError("Unsupported scrape mode.")
    pages = max(1, min(int(pages), PAGE_CAP))
    limit = max(1, min(int(limit), RESULT_CAP))
    client = get_client(provider)

    if mode == "search":
        method_name = "search_videos" if callable(getattr(client, "search_videos", None)) else "search"
        method = getattr(client, method_name, None)
        if not callable(method):
            raise RuntimeError(f"{get_spec(provider).name} does not expose search.")
        kwargs = _kwargs_for(method, pages, start_page)
        value = method(query, **kwargs)
    else:
        method_name, method = _method(client, provider, mode)
        if method is None:
            raise RuntimeError(f"{get_spec(provider).name} API does not expose {mode} listing.")
        kwargs = _kwargs_for(method, pages, start_page)
        if mode in {"category", "profile"}:
            if not query:
                raise ValueError("A category or profile name/URL is required.")
            value = method(query, **kwargs)
        else:
            # Methods with required sort arguments are handled with sensible defaults.
            required = [p for p in inspect.signature(method).parameters.values()
                        if p.default is inspect.Parameter.empty
                        and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
                        and p.name not in kwargs]
            if required:
                first = required[0].name
                if first in {"sort", "sort_by", "listing"}:
                    kwargs[first] = "popular"
            value = method(**kwargs)

    results = await _items(value, provider, limit)
    return ScrapeOutcome(results, method_name, pages, len(results) >= limit)


async def get_categories(provider: str) -> list[dict]:
    client = get_client(provider)
    method_name, method = _method(client, provider, "categories")
    if method is None:
        raise RuntimeError(f"{get_spec(provider).name} API does not expose category listings.")
    value = await _resolve(method())
    if hasattr(value, "__aiter__"):
        raw = []
        async for item in value:
            raw.append(await _resolve(item))
            if len(raw) >= 100:
                break
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)[:100]
    elif isinstance(value, dict):
        raw = list(value.values())[:100]
    else:
        raw = []
    output, seen = [], set()
    for item in raw:
        if isinstance(item, str):
            name, url = item, item
        elif isinstance(item, dict):
            name = item.get("name") or item.get("title") or item.get("label")
            url = item.get("url") or item.get("link") or item.get("href") or name
        else:
            name = getattr(item, "name", None) or getattr(item, "title", None)
            url = getattr(item, "url", None) or getattr(item, "link", None) or name
        if name and url and str(url) not in seen:
            seen.add(str(url))
            output.append({"name": str(name), "url": str(url)})
    return output
