from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu(provider: str = "eporner") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔎  Search videos", callback_data="menu:search")
    kb.button(
        text=f"🔁  Source: {'ePorner' if provider == 'eporner' else 'Beeg'}",
        callback_data="menu:provider",
    )
    kb.button(text="📊  Active tasks", callback_data="menu:tasks")
    kb.button(text="⚙️  Settings", callback_data="menu:settings")
    kb.adjust(1, 1, 2)
    return kb.as_markup()


def gallery_keyboard(index: int, total: int, url: str, provider: str = "eporner") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Previous", callback_data="gallery:prev")
    kb.button(text=f"{index + 1} / {total}", callback_data="gallery:page")
    kb.button(text="Next ▶️", callback_data="gallery:next")
    kb.button(text="⬇️ Download this video", callback_data="gallery:download")
    kb.button(text=f"↗️ Open on {'ePorner' if provider == 'eporner' else 'Beeg'}", url=url)
    kb.button(text="🏠 Main menu", callback_data="menu:home")
    kb.adjust(3, 1, 1, 1)
    return kb.as_markup()


def search_result_keyboard(url: str, index: int) -> InlineKeyboardMarkup:
    return gallery_keyboard(index - 1, index, url)


def video_actions(url: str) -> InlineKeyboardMarkup:
    return search_result_keyboard(url, 1)


def settings_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠  Main menu", callback_data="menu:home")
    return kb.as_markup()
