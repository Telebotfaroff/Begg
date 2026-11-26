from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    key: str
    name: str
    module: str
    domains: tuple[str, ...]
    mode: str = "hls"


# All video API projects found under EchterAlsFake, excluding Pornhub.
# Package import names and RAW/HLS modes follow each project's published README.
PROVIDERS: dict[str, ProviderSpec] = {
    "eporner": ProviderSpec("eporner", "ePorner", "eporner_api", ("eporner.com",), "raw"),
    "xvideos": ProviderSpec("xvideos", "XVideos", "xvideos_api", ("xvideos.com",), "hls"),
    "xnxx": ProviderSpec("xnxx", "XNXX", "xnxx_api", ("xnxx.com",), "hls"),
    "xhamster": ProviderSpec("xhamster", "xHamster", "xhamster_api", ("xhamster.com",), "hls"),
    "youporn": ProviderSpec("youporn", "YouPorn", "youporn_api", ("youporn.com",), "hls"),
    "xfreehd": ProviderSpec("xfreehd", "XFreeHD", "xfreehd_api", ("xfreehd.com",), "raw"),
    "hqporner": ProviderSpec("hqporner", "HQPorner", "hqporner_api", ("hqporner.com",), "raw"),
    "redtube": ProviderSpec("redtube", "RedTube", "redtube_api", ("redtube.com",), "hls"),
    "tube8": ProviderSpec("tube8", "Tube8", "tube8_api", ("tube8.com",), "hls"),
    "thumbzilla": ProviderSpec("thumbzilla", "Thumbzilla", "thumbzilla_api", ("thumbzilla.com",), "hls"),
    "tnaflix": ProviderSpec("tnaflix", "TNAFlix", "tnaflix_api", ("tnaflix.com",), "raw"),
    "bellesa": ProviderSpec("bellesa", "Bellesa", "bellesa_api", ("bellesa.co", "bellesa.com"), "hls"),
    "empflix": ProviderSpec("empflix", "Empflix", "empflix_api", ("empflix.com",), "raw"),
    "pornhat": ProviderSpec("pornhat", "PornHat", "pornhat_api", ("pornhat.com",), "hls"),
    "youjizz": ProviderSpec("youjizz", "YouJizz", "youjizz_api", ("youjizz.com",), "hls"),
}

DEFAULT_PROVIDER = "eporner"


def get_spec(key: str) -> ProviderSpec:
    try:
        return PROVIDERS[key]
    except KeyError as exc:
        raise ValueError(f"Unknown provider: {key}") from exc


def provider_for_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip() if "://" in value else f"https://{value.strip()}")
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.").rstrip(".")
    for key, spec in PROVIDERS.items():
        if any(host == domain or host.endswith("." + domain) for domain in spec.domains):
            return key
    return None


_CLIENTS: dict[str, object] = {}
_MODULES: dict[str, object] = {}


def _module(key: str):
    spec = get_spec(key)
    if key not in _MODULES:
        try:
            _MODULES[key] = importlib.import_module(spec.module)
        except ImportError as exc:
            raise RuntimeError(
                f"{spec.name} API package is not installed. Reinstall requirements.txt."
            ) from exc
    return _MODULES[key]


def get_client(key: str):
    if key not in _CLIENTS:
        module = _module(key)
        client_type = getattr(module, "Client", None)
        if client_type is None:
            raise RuntimeError(f"{get_spec(key).name} API does not expose Client.")
        _CLIENTS[key] = client_type()
    return _CLIENTS[key]


def _normalise_video(video, provider: str) -> dict | None:
    video_id = (
        getattr(video, "video_id", None)
        or getattr(video, "id", None)
        or getattr(video, "content_id", None)
    )
    url = getattr(video, "url", None) or getattr(video, "video_url", None)
    title = getattr(video, "title", None) or getattr(video, "name", None)
    if not url or not title:
        return None
    if not video_id:
        video_id = str(url).rstrip("/").split("/")[-1].split("?")[0]
    performers = getattr(video, "performers", None) or []
    creator = (
        getattr(video, "author_name", None)
        or getattr(video, "author", None)
        or getattr(video, "uploader", None)
        or getattr(video, "creator", None)
    )
    if not creator and performers and isinstance(performers[0], dict):
        creator = performers[0].get("name")
    thumbnail = (
        getattr(video, "thumbnail", None)
        or getattr(video, "thumbnail_url", None)
        or getattr(video, "thumb", None)
        or getattr(video, "preview", None)
    )
    duration = (
        getattr(video, "length", None)
        or getattr(video, "length_seconds", None)
        or getattr(video, "duration", None)
    )
    return {
        "title": str(title),
        "url": str(url),
        "video_id": str(video_id),
        "thumbnail": str(thumbnail) if thumbnail else None,
        "creator": str(creator) if creator else None,
        "duration": str(duration) if duration else None,
        "views": getattr(video, "views", None),
        "uploaded": getattr(video, "publish_date", None) or getattr(video, "upload_date", None),
        "provider": provider,
    }


async def _collect(value, provider: str, limit: int) -> list[dict]:
    if inspect.isawaitable(value):
        value = await value
    if hasattr(value, "unwrap") and callable(value.unwrap):
        value = value.unwrap()
    if hasattr(value, "__aiter__"):
        raw = []
        async for item in value:
            if hasattr(item, "unwrap") and callable(item.unwrap):
                item = item.unwrap()
            raw.append(item)
            if len(raw) >= limit:
                break
    elif isinstance(value, (list, tuple)):
        raw = list(value)[:limit]
    else:
        raw = [value]
    results, seen = [], set()
    for item in raw:
        mapped = _normalise_video(item, provider)
        if mapped and mapped["url"] not in seen:
            seen.add(mapped["url"])
            results.append(mapped)
    return results[:limit]


async def search_videos(provider: str, query: str, limit: int = 10) -> list[dict]:
    client = get_client(provider)
    method = getattr(client, "search_videos", None) or getattr(client, "search", None)
    if method is None:
        raise RuntimeError(f"{get_spec(provider).name} API does not expose video search.")
    attempts = (
        lambda: method(query, per_page=limit, pages=1),
        lambda: method(query, limit=limit),
        lambda: method(query, per_page=limit),
        lambda: method(query),
    )
    last_error = None
    for call in attempts:
        try:
            return await _collect(call(), provider, limit)
        except TypeError as exc:
            last_error = exc
    raise RuntimeError(f"{get_spec(provider).name} search signature is not supported: {last_error}")


async def get_video(provider: str, url: str):
    client = get_client(provider)
    method = getattr(client, "get_video", None)
    if method is None:
        raise RuntimeError(f"{get_spec(provider).name} API does not expose get_video().")
    return await method(url)


def make_download_config(provider: str, quality: str, path: str):
    module = _module(provider)
    spec = get_spec(provider)
    config_type = getattr(module, "DownloadConfigRAW" if spec.mode == "raw" else "DownloadConfigHLS", None)
    if config_type is None:
        config_type = getattr(module, "DownloadConfigHLS", None) or getattr(module, "DownloadConfigRAW", None)
    if config_type is None:
        try:
            shared = importlib.import_module("base_api")
            config_type = getattr(shared, "DownloadConfigHLS", None) or getattr(shared, "DownloadConfigRAW", None)
        except ImportError:
            config_type = None
    if config_type is None:
        return None
    return config_type(quality=quality, path=path)
