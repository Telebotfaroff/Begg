from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass

from services.providers import PROVIDERS


@dataclass(slots=True)
class ProviderHealth:
    key: str
    name: str
    status: str
    detail: str


def _inspect_provider(key: str, spec) -> ProviderHealth:
    try:
        module = importlib.import_module(spec.module)
    except Exception as exc:
        return ProviderHealth(key, spec.name, "missing", f"Package unavailable: {type(exc).__name__}")

    client_type = getattr(module, "Client", None)
    if client_type is None:
        return ProviderHealth(key, spec.name, "incomplete", "Client class not found")

    try:
        client = client_type()
    except Exception as exc:
        return ProviderHealth(key, spec.name, "incomplete", f"Client init failed: {type(exc).__name__}")

    search = callable(getattr(client, "search_videos", None)) or callable(getattr(client, "search", None))
    video = callable(getattr(client, "get_video", None))
    if not search or not video:
        missing = []
        if not search:
            missing.append("search")
        if not video:
            missing.append("get_video")
        return ProviderHealth(key, spec.name, "incomplete", "Missing API method: " + ", ".join(missing))

    return ProviderHealth(key, spec.name, "ready", "Package and required API methods found")


async def check_all_providers() -> list[ProviderHealth]:
    checks = [
        asyncio.to_thread(_inspect_provider, key, spec)
        for key, spec in PROVIDERS.items()
    ]
    return await asyncio.gather(*checks)
