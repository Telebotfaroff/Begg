from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from beeg_api import Client as BeegClient, DownloadConfigHLS
from wzgram import Client as WZClient
from config import settings
from services.beeg import search_videos, format_duration, clean_video_url
from ui.keyboards import main_menu, search_result_keyboard, video_actions, settings_keyboard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("begg")

router = Router()
download_slots = asyncio.Semaphore(settings.download_concurrency)
upload_slots = asyncio.Semaphore(settings.upload_concurrency)
active_downloads: dict[str, dict] = {}
beeg = BeegClient()
uploader: WZClient | None = None


class SearchFlow(StatesGroup):
    waiting_query = State()


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id == settings.admin_id


async def deny(message: Message) -> None:
    await message.answer("🔒 This is a private bot. You are not on the admin list.")


def pretty_size(value: int | float) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def progress_bar(percent: float, width: int = 12) -> str:
    filled = max(0, min(width, int(width * percent / 100)))
    return "▰" * filled + "▱" * (width - filled)


def task_id(chat_id: int, message_id: int) -> str:
    return f"{chat_id}:{message_id}"


async def start_wzgram() -> WZClient:
    client = WZClient(
        name="begg-uploader",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        workdir=".",
    )
    await client.start()
    log.info("WZGram MTProto uploader started")
    return client


async def send_progress(message: Message, text: str) -> None:
    try:
        await message.edit_text(text)
    except Exception:
        pass


async def upload_file(path: Path, title: str, progress_message: Message) -> None:
    if uploader is None:
        raise RuntimeError("WZGram uploader is not initialized")

    started = time.monotonic()
    last_update = 0.0
    last_bytes = 0
    last_time = started
    file_size = path.stat().st_size

    async def progress(current: int, total: int) -> None:
        nonlocal last_update, last_bytes, last_time
        now = time.monotonic()
        if now - last_update < settings.update_interval and current < total:
            return
        elapsed = max(now - started, 0.001)
        delta_time = max(now - last_time, 0.001)
        speed = max(0, current - last_bytes) / delta_time
        percent = (current / total * 100) if total else 0
        remaining = max(0, total - current)
        eta = remaining / max(speed, 1) if speed else 0
        text = (
            "📤 <b>Uploading to Telegram</b>\n"
            f"<blockquote>{title[:180]}</blockquote>\n"
            f"{progress_bar(percent)} <b>{percent:.1f}%</b>\n"
            f"📦 {pretty_size(current)} / {pretty_size(total or file_size)}\n"
            f"⚡ {pretty_size(speed)}/s  ·  ⏳ {int(eta)}s left"
        )
        await send_progress(progress_message, text)
        last_update, last_bytes, last_time = now, current, now

    await uploader.send_video(
        chat_id=settings.admin_id,
        video=str(path),
        caption=f"🎬 <b>{title[:900]}</b>",
        supports_streaming=True,
        progress=progress,
    )


async def run_download(video_url: str, title_hint: str, origin: Message) -> None:
    key = task_id(origin.chat.id, origin.message_id)
    task_dir = Path(settings.download_dir) / str(origin.chat.id) / str(origin.message_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    status = await origin.answer(
        "⏳ <b>Preparing download</b>\n"
        f"<blockquote>{title_hint[:180]}</blockquote>\n"
        "Waiting for a download slot…"
    )
    active_downloads[key] = {"title": title_hint, "status": "queued", "message": status}

    try:
        async with download_slots:
            active_downloads[key]["status"] = "downloading"
            await send_progress(
                status,
                "⬇️ <b>Downloading</b>\n"
                f"<blockquote>{title_hint[:180]}</blockquote>\n"
                "▰▱▱▱▱▱▱▱▱▱▱▱  Preparing stream…",
            )
            video = await beeg.get_video(video_url)
            title = getattr(video, "title", None) or title_hint
            config = DownloadConfigHLS(
                quality=settings.video_quality,
                path=str(task_dir),
                no_title=False,
                return_report=True,
            )

            download_started = time.monotonic()
            download_task = asyncio.create_task(video.download(config))
            last_size = -1
            while not download_task.done():
                await asyncio.sleep(settings.update_interval)
                current_size = sum(p.stat().st_size for p in task_dir.rglob("*") if p.is_file())
                if current_size != last_size:
                    elapsed = max(time.monotonic() - download_started, 0.001)
                    speed = current_size / elapsed
                    await send_progress(
                        status,
                        "⬇️ <b>Downloading</b>\n"
                        f"<blockquote>{title[:180]}</blockquote>\n"
                        f"📦 {pretty_size(current_size)} downloaded\n"
                        f"⚡ {pretty_size(speed)}/s  ·  ⏳ ETA calculating\n"
                        "<i>Live HLS transfer — final size is confirmed when complete.</i>",
                    )
                    last_size = current_size
            await download_task

        candidates = [p for p in task_dir.rglob("*") if p.is_file() and p.stat().st_size > 0]
        if not candidates:
            raise RuntimeError("The downloader finished but no output file was created.")
        output = max(candidates, key=lambda p: p.stat().st_size)
        actual_title = getattr(video, "title", None) or title
        size = output.stat().st_size

        active_downloads[key]["status"] = "waiting_upload"
        await send_progress(
            status,
            "✅ <b>Download complete</b>\n"
            f"<blockquote>{actual_title[:180]}</blockquote>\n"
            f"📦 {pretty_size(size)}\n"
            "⏳ Waiting for an upload slot…",
        )

        async with upload_slots:
            active_downloads[key]["status"] = "uploading"
            await upload_file(output, actual_title, status)

        await send_progress(
            status,
            "✨ <b>Transfer complete</b>\n"
            f"<blockquote>{actual_title[:180]}</blockquote>\n"
            f"📦 {pretty_size(size)}\n"
            "📤 Sent to your Telegram chat.",
        )
    except asyncio.CancelledError:
        await send_progress(status, "⏹️ <b>Task cancelled.</b>")
        raise
    except Exception as exc:
        log.exception("Task failed: %s", key)
        await send_progress(status, f"❌ <b>Task failed</b>\n<code>{str(exc)[:700]}</code>")
    finally:
        active_downloads.pop(key, None)
        shutil.rmtree(task_dir, ignore_errors=True)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return await deny(message)
    await state.clear()
    await message.answer(
        "╭━━━ <b>BEGG</b> ━━━╮\n"
        "│  <i>Your personal Beeg media assistant</i>\n"
        "╰━━━━━━━━━━━━━━╯\n\n"
        "Welcome back! Choose an action below.\n\n"
        "🔎 <b>Search</b> videos by keyword\n"
        "🔗 <b>Open link</b> using a Beeg URL or video ID\n"
        "📊 <b>Tasks</b> view current activity",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return await deny(message)
    await state.clear()
    await message.answer("Cancelled. You're back at the main menu.", reply_markup=main_menu())


@router.callback_query(F.data == "menu:search")
async def search_button(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    await state.set_state(SearchFlow.waiting_query)
    await call.message.edit_text(
        "🔎 <b>Find a video</b>\nSend a search phrase or paste a Beeg video URL.",
        reply_markup=InlineKeyboardBuilder().button(text="✕ Cancel", callback_data="menu:home").as_markup(),
    )
    await call.answer()


@router.callback_query(F.data == "menu:home")
async def home_button(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    await state.clear()
    await call.message.edit_text("╭━━━ <b>BEGG</b> ━━━╮\n│  <i>Personal media assistant</i>\n╰━━━━━━━━━━━━━━╯\nChoose an action:", reply_markup=main_menu())
    await call.answer()


@router.callback_query(F.data == "menu:tasks")
async def tasks_button(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    if not active_downloads:
        text = "📊 <b>Task monitor</b>\n\n✨ No active tasks right now."
    else:
        rows = []
        for item in active_downloads.values():
            icon = {"queued": "🕓", "downloading": "⬇️", "waiting_upload": "📦", "uploading": "📤"}.get(item["status"], "•")
            rows.append(f"{icon} <b>{item['title'][:70]}</b> — {item['status'].replace('_', ' ')}")
        text = "📊 <b>Task monitor</b>\n\n" + "\n".join(rows)
    await call.message.edit_text(text, reply_markup=main_menu())
    await call.answer()


@router.callback_query(F.data == "menu:settings")
async def settings_button(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    await call.message.edit_text(
        "⚙️ <b>Bot settings</b>\n\n"
        f"⬇️ Download slots: <b>{settings.download_concurrency}</b>\n"
        f"📤 Upload slots: <b>{settings.upload_concurrency}</b>\n"
        f"🎞 Quality: <b>{settings.video_quality}</b>\n"
        f"🔄 Progress refresh: <b>{settings.update_interval}s</b>",
        reply_markup=settings_keyboard(),
    )
    await call.answer()


@router.message(SearchFlow.waiting_query)
async def process_search(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return await deny(message)
    query = (message.text or "").strip()
    if not query:
        return await message.answer("Please send a search phrase or Beeg URL.")
    await state.clear()

    if clean_video_url(query):
        await run_download(query, "Beeg video", message)
        return

    wait = await message.answer(f"🔎 <b>Searching Beeg…</b>\n<code>{query[:100]}</code>")
    try:
        results = await search_videos(query, settings.search_limit)
    except Exception as exc:
        log.exception("Search failed")
        await wait.edit_text(f"⚠️ Search failed: <code>{str(exc)[:500]}</code>", reply_markup=main_menu())
        return

    if not results:
        await wait.edit_text(
            "No results found. Try another phrase or paste a direct Beeg video URL.",
            reply_markup=main_menu(),
        )
        return

    await wait.delete()
    for index, item in enumerate(results, 1):
        caption = (
            f"🎬 <b>{item.title[:180]}</b>\n"
            f"🆔 <code>{item.video_id}</code>\n"
            f"🔗 <a href=\"{item.url}\">Open on Beeg</a>"
        )
        await message.answer(caption, reply_markup=search_result_keyboard(item.url, index))


@router.callback_query(F.data.startswith("get:"))
async def select_video(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    video_id = call.data.split(":", 1)[1]
    url = f"https://beeg.com/-0{video_id}"
    await call.answer("Added to download pipeline")
    await run_download(url, f"Beeg video {video_id}", call.message)


async def main():
    global uploader
    if not settings.bot_token or not settings.admin_id or not settings.api_id or not settings.api_hash:
        raise RuntimeError("Set BOT_TOKEN, ADMIN_ID, API_ID and API_HASH in your environment.")
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    uploader = await start_wzgram()
    log.info("BEGG started | downloads=%s uploads=%s", settings.download_concurrency, settings.upload_concurrency)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await uploader.stop()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
