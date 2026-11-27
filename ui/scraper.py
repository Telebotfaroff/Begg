from __future__ import annotations

import csv
import io
import json
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.providers import PROVIDERS, get_spec
from services.scraper import scrape, get_categories, PAGE_CAP, RESULT_CAP

log = logging.getLogger("begg.scraper")
router = Router()
CATEGORY_CACHE: dict[int, list[dict]] = {}
RESULT_CACHE: dict[int, list[dict]] = {}


class ScraperFlow(StatesGroup):
    waiting_parameters = State()


def _provider_keyboard(page: int = 0):
    kb = InlineKeyboardBuilder()
    keys = list(PROVIDERS)
    size = 8
    pages = max(1, (len(keys) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    for key in keys[page * size:(page + 1) * size]:
        kb.button(text=PROVIDERS[key].name, callback_data=f"sc:provider:{key}")
    kb.adjust(2)
    if pages > 1:
        if page:
            kb.button(text="◀️ Previous", callback_data=f"sc:page:{page - 1}")
        kb.button(text=f"{page + 1}/{pages}", callback_data="sc:noop")
        if page < pages - 1:
            kb.button(text="Next ▶️", callback_data=f"sc:page:{page + 1}")
        kb.adjust(2, 2, 2, 2, 2)
    kb.button(text="🏠 Main menu", callback_data="menu:home")
    return kb.as_markup()


def _provider_actions(provider: str):
    name = get_spec(provider).name
    kb = InlineKeyboardBuilder()
    kb.button(text="🔎 Bulk search", callback_data=f"sc:mode:{provider}:search")
    kb.button(text="🏠 Homepage listings", callback_data=f"sc:mode:{provider}:homepage")
    kb.button(text="🗂 Categories", callback_data=f"sc:categories:{provider}")
    kb.button(text="👤 Creator / channel / profile", callback_data=f"sc:mode:{provider}:profile")
    kb.button(text="🔁 Choose another provider", callback_data="menu:scraper")
    kb.button(text="🏠 Main menu", callback_data="menu:home")
    kb.adjust(1)
    return f"🧰 <b>Scraper Center · {name}</b>\n\nChoose what to collect. Available options depend on the methods exposed by this provider's API.", kb.as_markup()


def _category_keyboard(provider: str, categories: list[dict], page: int = 0):
    kb = InlineKeyboardBuilder()
    size = 8
    pages = max(1, (len(categories) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    for index, item in enumerate(categories[page * size:(page + 1) * size], page * size):
        label = item["name"][:50]
        kb.button(text=label, callback_data=f"sc:category:{provider}:{index}")
    kb.adjust(1)
    if pages > 1:
        if page:
            kb.button(text="◀️ Previous", callback_data=f"sc:catpage:{provider}:{page - 1}")
        kb.button(text=f"{page + 1}/{pages}", callback_data="sc:noop")
        if page < pages - 1:
            kb.button(text="Next ▶️", callback_data=f"sc:catpage:{provider}:{page + 1}")
        kb.adjust(1, 3)
    kb.button(text="↩️ Provider menu", callback_data=f"sc:provider:{provider}")
    kb.button(text="🏠 Main menu", callback_data="menu:home")
    return kb.as_markup()


def _parse_pages(value: str) -> int:
    if value.strip().lower() in {"all", "*"}:
        return PAGE_CAP
    return max(1, min(int(value.strip()), PAGE_CAP))


def _parse_parameters(text: str, mode: str, category_url: str = ""):
    parts = [part.strip() for part in text.split("|")]
    if mode in {"search", "profile"}:
        query = category_url or (parts[0] if parts else "")
        if not query:
            raise ValueError("Enter a search phrase or profile URL.")
        pages = _parse_pages(parts[1]) if len(parts) > 1 and parts[1] else 5
        limit = min(RESULT_CAP, max(1, int(parts[2]))) if len(parts) > 2 and parts[2] else 100
    else:
        query = category_url
        pages = _parse_pages(parts[0]) if parts and parts[0] else 5
        limit = min(RESULT_CAP, max(1, int(parts[1]))) if len(parts) > 1 and parts[1] else 100
    return query, pages, limit


async def _begin_mode(call: CallbackQuery, state: FSMContext, provider: str, mode: str, category_url: str = ""):
    if provider not in PROVIDERS:
        return await call.answer("Unknown provider.", show_alert=True)
    await state.clear()
    await state.update_data(scraper_provider=provider, scraper_mode=mode, scraper_category=category_url)
    await state.set_state(ScraperFlow.waiting_parameters)
    name = get_spec(provider).name
    if mode in {"search", "profile"}:
        prompt = "Send: <code>search phrase or profile URL | pages | max results</code>"
    elif mode == "category":
        prompt = "Send: <code>pages | max results</code>"
    else:
        prompt = "Send: <code>pages | max results</code>"
    await call.message.edit_text(
        f"🧰 <b>{name} · {mode.title()} scraper</b>\n\n"
        f"{prompt}\n\n"
        f"Example: <code>{'retro | 5 | 100' if mode in {'search', 'profile'} else '5 | 100'}</code>\n"
        f"Use <code>all</code> for up to {PAGE_CAP} pages. Hard cap: {RESULT_CAP} results.\n\n"
        "Send <code>/cancel</code> to leave this screen.",
    )
    await call.answer()


@router.callback_query(F.data == "menu:scraper")
async def scraper_home(call: CallbackQuery, state: FSMContext):
    if call.from_user.id is None:
        return await call.answer("Not authorized", show_alert=True)
    await state.clear()
    await call.message.edit_text(
        "🧰 <b>BEGG Scraper Center</b>\n\n"
        "Choose a source to scrape. Bulk scraping is capped at 20 pages and 500 results per run. "
        "Results are deduplicated by video URL and exported as temporary JSON/CSV files.",
        reply_markup=_provider_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "sc:noop")
async def scraper_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("sc:page:"))
async def scraper_page(call: CallbackQuery):
    page = int(call.data.rsplit(":", 1)[-1])
    await call.message.edit_reply_markup(reply_markup=_provider_keyboard(page))
    await call.answer()


@router.callback_query(F.data.startswith("sc:provider:"))
async def scraper_provider(call: CallbackQuery, state: FSMContext):
    provider = call.data.split(":", 2)[2]
    if provider not in PROVIDERS:
        return await call.answer("Unknown provider.", show_alert=True)
    text, markup = _provider_actions(provider)
    await state.clear()
    await call.message.edit_text(text, reply_markup=markup)
    await call.answer()


@router.callback_query(F.data.startswith("sc:mode:"))
async def scraper_mode(call: CallbackQuery, state: FSMContext):
    _, _, provider, mode = call.data.split(":", 3)
    await _begin_mode(call, state, provider, mode)


@router.callback_query(F.data.startswith("sc:categories:"))
async def scraper_categories(call: CallbackQuery, state: FSMContext):
    provider = call.data.split(":", 2)[2]
    if provider not in PROVIDERS:
        return await call.answer("Unknown provider.", show_alert=True)
    await call.answer("Loading categories…")
    try:
        categories = await get_categories(provider)
        if not categories:
            raise RuntimeError("The API returned no categories.")
        CATEGORY_CACHE[call.from_user.id] = categories
        await state.update_data(scraper_provider=provider)
        await call.message.edit_text(
            f"🗂 <b>{get_spec(provider).name} categories</b>\n\nSelect a category to scrape ({len(categories)} found).",
            reply_markup=_category_keyboard(provider, categories),
        )
    except Exception as exc:
        await call.message.edit_text(
            f"⚠️ <b>Categories unavailable</b>\n<code>{str(exc)[:450]}</code>",
            reply_markup=_provider_actions(provider)[1],
        )


@router.callback_query(F.data.startswith("sc:catpage:"))
async def scraper_category_page(call: CallbackQuery):
    _, _, provider, page = call.data.split(":", 3)
    categories = CATEGORY_CACHE.get(call.from_user.id, [])
    if not categories:
        return await call.answer("Category list expired. Open Categories again.", show_alert=True)
    await call.message.edit_reply_markup(reply_markup=_category_keyboard(provider, categories, int(page)))
    await call.answer()


@router.callback_query(F.data.startswith("sc:category:"))
async def scraper_category(call: CallbackQuery, state: FSMContext):
    _, _, provider, index = call.data.split(":", 3)
    categories = CATEGORY_CACHE.get(call.from_user.id, [])
    try:
        category = categories[int(index)]
    except (IndexError, ValueError):
        return await call.answer("Category list expired. Reload it.", show_alert=True)
    await _begin_mode(call, state, provider, "category", category["url"])


@router.message(ScraperFlow.waiting_parameters)
async def scraper_run(message: Message, state: FSMContext):
    if not message.from_user:
        return
    data = await state.get_data()
    provider = data.get("scraper_provider")
    mode = data.get("scraper_mode")
    category_url = data.get("scraper_category", "")
    try:
        query, pages, limit = _parse_parameters(message.text or "", mode, category_url)
    except Exception as exc:
        return await message.answer(f"⚠️ {str(exc)}\nTry again using the requested format.")
    await state.clear()
    status = await message.answer(
        f"⏳ <b>Scraping {get_spec(provider).name}</b>\n"
        f"Mode: {mode.title()} · Pages: {pages} · Max results: {limit}\n"
        "Collecting and deduplicating metadata…"
    )
    try:
        outcome = await scrape(provider, mode, query=query, pages=pages, limit=limit)
        items = outcome.items
        RESULT_CACHE[message.from_user.id] = items
        if not items:
            return await status.edit_text(
                f"⚠️ No usable video records returned. API method: <code>{outcome.method}</code>.",
                reply_markup=_provider_actions(provider)[1],
            )
        kb = InlineKeyboardBuilder()
        kb.button(text="📄 Export JSON", callback_data="sc:export:json")
        kb.button(text="📊 Export CSV", callback_data="sc:export:csv")
        kb.button(text="🧰 Scraper Center", callback_data="menu:scraper")
        kb.button(text="🏠 Main menu", callback_data="menu:home")
        kb.adjust(2, 1, 1)
        await status.edit_text(
            f"✅ <b>Scrape complete</b>\n\n"
            f"📡 Source: <b>{get_spec(provider).name}</b>\n"
            f"🧩 Mode: {mode.title()}\n"
            f"🔧 API method: <code>{outcome.method}</code>\n"
            f"📚 Pages requested: {outcome.pages_requested}\n"
            f"🎬 Unique videos: <b>{len(items)}</b>\n"
            f"{'⚠️ Result cap reached; more may be available.' if outcome.truncated else '✨ Listing finished.'}\n\n"
            "Choose an export format. Files are temporary and are not stored in a database.",
            reply_markup=kb.as_markup(),
        )
    except Exception as exc:
        log.exception("Scraper run failed")
        await status.edit_text(
            f"❌ <b>Scrape failed</b>\n<code>{str(exc)[:650]}</code>",
            reply_markup=_provider_actions(provider)[1],
        )


@router.callback_query(F.data.startswith("sc:export:"))
async def scraper_export(call: CallbackQuery):
    items = RESULT_CACHE.get(call.from_user.id, [])
    if not items:
        return await call.answer("No scrape results in memory. Run a scrape first.", show_alert=True)
    kind = call.data.rsplit(":", 1)[-1]
    if kind == "json":
        content = json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8")
        filename = "begg_scrape.json"
    elif kind == "csv":
        buffer = io.StringIO()
        fields = ["title", "url", "video_id", "provider", "creator", "duration", "views", "uploaded", "thumbnail"]
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)
        content = buffer.getvalue().encode("utf-8-sig")
        filename = "begg_scrape.csv"
    else:
        return await call.answer("Unknown export format.", show_alert=True)
    await call.answer("Preparing export…")
    await call.message.answer_document(BufferedInputFile(content, filename=filename), caption=f"📦 {len(items)} unique records · {filename}")
