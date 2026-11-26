from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔎  Search videos", callback_data="menu:search")
    kb.button(text="📊  Active tasks", callback_data="menu:tasks")
    kb.button(text="⚙️  Settings", callback_data="menu:settings")
    kb.adjust(1, 2)
    return kb.as_markup()


def search_result_keyboard(url: str, index: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"⬇️  Download #{index}", callback_data=f"get:{url}")
    kb.button(text="↗️  Open on Beeg", url=url)
    kb.adjust(1, 1)
    return kb.as_markup()


def video_actions(url: str) -> InlineKeyboardMarkup:
    return search_result_keyboard(url, 1)


def settings_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠  Main menu", callback_data="menu:home")
    return kb.as_markup()
