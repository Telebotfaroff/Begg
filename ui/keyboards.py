from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from services.providers import PROVIDERS


def main_menu(provider: str = "eporner") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔎  Search videos", callback_data="menu:search")
    spec = PROVIDERS.get(provider)
    kb.button(text=f"🔁  Source: {spec.name if spec else provider}", callback_data="menu:provider")
    kb.button(text="📊  Active tasks", callback_data="menu:tasks")
    kb.button(text="🖥️  System monitor", callback_data="menu:system")
    kb.button(text="🩺  Provider health", callback_data="menu:health")
    kb.button(text="⚙️  Settings", callback_data="menu:settings")
    kb.adjust(1, 1, 2, 1, 1)
    return kb.as_markup()


def provider_picker_keyboard(active: str, page: int = 0) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    keys = list(PROVIDERS)
    page_size = 8
    page_count = (len(keys) + page_size - 1) // page_size
    page = max(0, min(page, page_count - 1))
    for key in keys[page * page_size:(page + 1) * page_size]:
        spec = PROVIDERS[key]
        kb.button(text=f"{'✓ ' if key == active else ''}{spec.name}", callback_data=f"provider:set:{key}")
    kb.adjust(2)
    if page_count > 1:
        if page > 0:
            kb.button(text="◀️ Previous", callback_data=f"provider:page:{page - 1}")
        kb.button(text=f"{page + 1}/{page_count}", callback_data="provider:noop")
        if page < page_count - 1:
            kb.button(text="Next ▶️", callback_data=f"provider:page:{page + 1}")
        kb.adjust(2, 2, 2, 2, 2)
    kb.button(text="🏠 Main menu", callback_data="menu:home")
    return kb.as_markup()


def gallery_keyboard(index: int, total: int, url: str, provider: str = "eporner") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Previous", callback_data="gallery:prev")
    kb.button(text=f"{index + 1} / {total}", callback_data="gallery:page")
    kb.button(text="Next ▶️", callback_data="gallery:next")
    kb.button(text="⬇️ Download this video", callback_data="gallery:download")
    spec = PROVIDERS.get(provider)
    kb.button(text=f"↗️ Open on {spec.name if spec else provider}", url=url)
    kb.button(text="🏠 Main menu", callback_data="menu:home")
    kb.adjust(3, 1, 1, 1)
    return kb.as_markup()


def search_result_keyboard(url: str, index: int) -> InlineKeyboardMarkup:
    return gallery_keyboard(index - 1, index, url)


def video_actions(url: str) -> InlineKeyboardMarkup:
    return search_result_keyboard(url, 1)


def settings_keyboard(quality: str, downloads: int, uploads: int, interval: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for value in ("best", "half", "worst"):
        kb.button(text=f"{'✓ ' if quality == value else ''}Quality: {value}", callback_data=f"settings:quality:{value}")
    for value in range(1, 6):
        kb.button(text=f"{'✓ ' if downloads == value else ''}⬇️ {value}", callback_data=f"settings:downloads:{value}")
    for value in (1, 2):
        kb.button(text=f"{'✓ ' if uploads == value else ''}📤 {value}", callback_data=f"settings:uploads:{value}")
    for value in (2, 3, 5, 10):
        kb.button(text=f"{'✓ ' if interval == value else ''}{value}s", callback_data=f"settings:interval:{value}")
    kb.button(text="🩺 Provider health", callback_data="menu:health")
    kb.button(text="🔄 Refresh settings", callback_data="menu:settings")
    kb.button(text="🏠 Main menu", callback_data="menu:home")
    kb.adjust(3, 5, 2, 4, 1, 1, 1)
    return kb.as_markup()
