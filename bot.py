from __future__ import annotations

import asyncio
import html
import json
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
from aiogram.types import CallbackQuery, Message, BufferedInputFile, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder

from eporner_api import DownloadConfigRAW
from eporner_api.modules.locals import Encoding as EpornerEncoding
from wzgram import Client as WZClient
from config import settings
from services.eporner import clean_video_url as clean_eporner_video_url, fetch_category_videos as fetch_eporner_category_videos, get_video as get_eporner_video, is_category_url as is_eporner_category_url, search_videos as search_eporner_videos
from services.providers import PROVIDERS, DEFAULT_PROVIDER, get_spec, get_video as get_generic_video, make_download_config, provider_for_url, search_videos as search_generic_videos
from services.provider_health import check_all_providers
from ui.keyboards import main_menu, gallery_keyboard, settings_keyboard, provider_picker_keyboard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("begg")

router = Router()
download_slots = asyncio.Semaphore(settings.download_concurrency)
upload_slots = asyncio.Semaphore(settings.upload_concurrency)
active_downloads: dict[str, dict] = {}
task_handles: dict[str, asyncio.Task] = {}
PROCESS_STARTED = time.monotonic()
ACTIVE_PROVIDER = DEFAULT_PROVIDER
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


def item_field(item, name: str, default=None):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


async def start_wzgram() -> WZClient:
    client = WZClient(
        name="begg-uploader",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        in_memory=True,
        no_updates=True,
        workdir=".",
    )
    await client.start()
    log.info("WZGram MTProto uploader started")
    return client


async def send_progress(message: Message, text: str) -> None:
    try:
        await message.edit_text(text)
    except Exception as exc:
        log.debug("Progress message update failed: %s", exc)


async def run_media_command(*args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def probe_video(path: Path) -> dict:
    code, stdout, stderr = await run_media_command(
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height",
        "-of", "json", str(path),
    )
    if code != 0:
        raise RuntimeError(f"ffprobe could not read the downloaded video: {stderr[-500:]}")
    return json.loads(stdout)


async def find_valid_video(task_dir: Path) -> Path:
    """Return the largest downloaded file that FFprobe confirms is a real video."""
    candidates = sorted(
        (p for p in task_dir.rglob("*") if p.is_file() and p.stat().st_size > 0),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    errors = []
    for candidate in candidates:
        try:
            info = await probe_video(candidate)
            if any(item.get("codec_type") == "video" for item in info.get("streams", [])):
                return candidate
            errors.append(f"{candidate.name}: no video stream")
        except Exception as exc:
            errors.append(f"{candidate.name}: {str(exc)[-250:]}")
    detail = "; ".join(errors[:3]) if errors else "no non-empty output files"
    raise RuntimeError(f"No valid video file was downloaded ({detail}).")


async def prepare_streamable_video(source: Path) -> tuple[Path, Path, dict]:
    """Create an MP4 suitable for Telegram streaming and a JPEG video thumbnail."""
    info = await probe_video(source)
    streams = info.get("streams", [])
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not video_stream:
        raise RuntimeError("Downloaded file has no video stream.")
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = int(float(info.get("format", {}).get("duration") or 0))
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)

    target = source.with_name(f"{source.stem}_telegram.mp4")
    thumb = source.with_name(f"{source.stem}_thumb.jpg")
    compatible = (
        source.suffix.lower() == ".mp4"
        and video_stream.get("codec_name") == "h264"
        and (audio_stream is None or audio_stream.get("codec_name") == "aac")
    )
    if compatible:
        # Re-mux with faststart so MP4 index metadata is at the beginning.
        args = ["ffmpeg", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a?"]
        args += ["-c", "copy", "-movflags", "+faststart", str(target)]
        code, _, stderr = await run_media_command(*args)
        if code != 0:
            compatible = False

    if not compatible:
        # Normalize unsupported containers/codecs to broadly playable H.264/AAC MP4.
        args = [
            "ffmpeg", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(target),
        ]
        code, _, stderr = await run_media_command(*args)
        if code != 0:
            raise RuntimeError(f"Could not prepare a Telegram-streamable MP4: {stderr[-700:]}")
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("FFmpeg did not create the prepared MP4.")

    # Telegram video thumbnails must be JPEG and no larger than 320x320.
    seek = max(0, min(1, duration // 2))
    code, _, stderr = await run_media_command(
        "ffmpeg", "-y", "-ss", str(seek), "-i", str(target),
        "-frames:v", "1", "-vf", "scale='min(320,iw)':'min(320,ih)':force_original_aspect_ratio=decrease",
        "-q:v", "4", str(thumb),
    )
    if code != 0 or not thumb.exists():
        log.warning("Thumbnail generation failed: %s", stderr[-400:])
        raise RuntimeError("Could not generate the Telegram video thumbnail.")
    if thumb.stat().st_size > 200 * 1024:
        code, _, stderr = await run_media_command(
            "ffmpeg", "-y", "-ss", str(seek), "-i", str(target),
            "-frames:v", "1", "-vf", "scale='min(240,iw)':'min(240,ih)':force_original_aspect_ratio=decrease",
            "-q:v", "8", str(thumb),
        )
        if code != 0 or not thumb.exists() or thumb.stat().st_size > 200 * 1024:
            raise RuntimeError("Generated Telegram thumbnail exceeds the 200 KB limit.")
    thumb_path = thumb
    final_info = await probe_video(target)
    final_video = next(item for item in final_info.get("streams", []) if item.get("codec_type") == "video")
    final_audio = next((item for item in final_info.get("streams", []) if item.get("codec_type") == "audio"), None)
    if final_video.get("codec_name") != "h264" or (final_audio and final_audio.get("codec_name") != "aac"):
        raise RuntimeError("Prepared video is not H.264/AAC compatible.")
    metadata = {
        "duration": int(float(final_info.get("format", {}).get("duration") or duration)),
        "width": int(final_video.get("width") or width),
        "height": int(final_video.get("height") or height),
    }
    return target, thumb_path, metadata


async def upload_file(path: Path, title: str, progress_message: Message, key: str) -> None:
    if uploader is None:
        raise RuntimeError("WZGram uploader is not initialized")

    path, thumbnail, metadata = await prepare_streamable_video(path)
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
        elapsed = max(now - started, 0.001)
        task = active_downloads.get(key)
        if task is not None:
            task.update({"current": current, "total": total or file_size, "speed": speed, "percent": percent, "eta": eta, "elapsed": elapsed})
        text = (
            "📤 <b>Uploading to Telegram</b>\n"
            f"<blockquote>{html.escape(title[:180])}</blockquote>\n"
            f"{progress_bar(percent)} <b>{percent:.1f}%</b>\n"
            f"📦 <b>{pretty_size(current)}</b> / {pretty_size(total or file_size)}\n"
            f"⚡ {pretty_size(speed)}/s  ·  ⏳ ETA {int(eta)}s\n"
            f"🕒 Elapsed: {int(elapsed)}s"
        )
        await send_progress(progress_message, text)
        last_update, last_bytes, last_time = now, current, now

    await uploader.send_video(
        chat_id=settings.admin_id,
        video=str(path),
        caption=f"🎬 <b>{html.escape(title[:700])}</b>",
        supports_streaming=True,
        duration=metadata["duration"],
        width=metadata["width"],
        height=metadata["height"],
        thumb=str(thumbnail) if thumbnail else None,
        file_name=path.name,
        progress=progress,
    )


async def run_download(video_url: str, title_hint: str, origin: Message, provider: str | None = None) -> None:
    provider = provider or ACTIVE_PROVIDER
    key = task_id(origin.chat.id, origin.message_id)
    task_dir = Path(settings.download_dir) / str(origin.chat.id) / str(origin.message_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    status = await origin.answer(
        "⏳ <b>Preparing download</b>\n"
        f"<blockquote>{html.escape(title_hint[:180])}</blockquote>\n"
        "Waiting for a download slot…"
    )
    active_downloads[key] = {"title": title_hint, "status": "queued", "message": status, "provider": provider, "created_at": time.time()}
    current_task = asyncio.current_task()
    if current_task is not None:
        task_handles[key] = current_task
    download_task: asyncio.Task | None = None

    try:
        async with download_slots:
            active_downloads[key]["status"] = "downloading"
            await send_progress(
                status,
                "⬇️ <b>Downloading</b>\n"
                f"<blockquote>{html.escape(title_hint[:180])}</blockquote>\n"
                f"▰▱▱▱▱▱▱▱▱▱▱▱  Preparing {('direct MP4' if provider == 'eporner' else get_spec(provider).mode.upper())} stream…",
            )
            if provider == "eporner":
                video = await get_eporner_video(video_url)
                title = getattr(video, "title", None) or title_hint
                author = getattr(video, "uploader", None)
                if not author:
                    performers = getattr(video, "performers", None) or []
                    author = performers[0].get("name") if performers and isinstance(performers[0], dict) else None
                duration = getattr(video, "length_seconds", 0) or 0
                tags = getattr(video, "keywords", None) or getattr(video, "tags", None) or []
                publish_date = getattr(video, "publish_date", None)
                config = DownloadConfigRAW(quality=settings.video_quality, path=str(task_dir))
                download_mode = "direct MP4"
            else:
                video = await get_generic_video(provider, video_url)
                title = getattr(video, "title", None) or title_hint
                author = getattr(video, "author_name", None) or getattr(video, "author", None) or getattr(video, "uploader", None)
                duration = getattr(video, "length_seconds", None) or getattr(video, "duration", 0) or 0
                tags = getattr(video, "tags", None) or []
                publish_date = getattr(video, "publish_date", None) or getattr(video, "upload_date", None)
                config = make_download_config(provider, settings.video_quality, str(task_dir))
                if config is None:
                    raise RuntimeError(f"{get_spec(provider).name} API has no supported download config.")
                download_mode = get_spec(provider).mode.upper()

            metadata_lines = []
            if author:
                metadata_lines.append(f"👤 {html.escape(str(author)[:100])}")
            if duration:
                try:
                    total_seconds = max(0, int(duration))
                    hours, remainder = divmod(total_seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    duration_text = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
                except (TypeError, ValueError):
                    duration_text = str(duration)
                metadata_lines.append(f"⏱ {html.escape(duration_text[:24])}")
            if publish_date:
                metadata_lines.append(f"📅 {html.escape(str(publish_date)[:60])}")
            if tags:
                tag_items = [tags] if isinstance(tags, str) else list(tags)
                tag_text = ", ".join(str(tag) for tag in tag_items[:6])
                metadata_lines.append(f"🏷 {html.escape(tag_text[:180])}")

            download_started = time.monotonic()
            if metadata_lines:
                await send_progress(
                    status,
                    "⬇️ <b>Downloading</b>\\n"
                    f"<blockquote>{html.escape(title[:180])}</blockquote>\\n"
                    + "\\n".join(metadata_lines)
                    + f"\\n\\nPreparing {download_mode} stream…",
                )
            if provider == "eporner":
                download_task = asyncio.create_task(video.download(configuration=config, mode=EpornerEncoding.mp4_h264))
            else:
                download_task = asyncio.create_task(video.download(configuration=config))

            # Measure bytes written between samples for live transfer speed.
            # The final sample also covers downloads that finish very quickly.
            last_sample_time = download_started
            last_sample_size = 0
            last_rendered_size = -1
            while not download_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(download_task),
                        timeout=max(1, min(settings.update_interval, 3)),
                    )
                except asyncio.TimeoutError:
                    pass

                now = time.monotonic()
                current_size = sum(
                    p.stat().st_size for p in task_dir.rglob("*")
                    if p.is_file()
                )
                delta_time = max(now - last_sample_time, 0.001)
                average_speed = current_size / max(now - download_started, 0.001)
                live_speed = max(0, current_size - last_sample_size) / delta_time

                if current_size != last_rendered_size or download_task.done():
                    shown_speed = live_speed if current_size > last_sample_size else average_speed
                    active_downloads[key].update({
                        "current": current_size,
                        "total": 0,
                        "speed": shown_speed,
                        "percent": None,
                        "elapsed": max(now - download_started, 0.0),
                    })
                    speed_label = f"{pretty_size(shown_speed)}/s" if current_size else "Waiting for data…"
                    await send_progress(
                        status,
                        "⬇️ <b>Downloading</b>\\n"
                        f"<blockquote>{html.escape(title[:180])}</blockquote>\\n"
                        f"📦 {pretty_size(current_size)} downloaded\\n"
                        f"⚡ {speed_label}  ·  ⏳ ETA unavailable until total size is known\\n"
                        f"<i>{download_mode} transfer · live speed from file growth.</i>",
                    )
                    last_rendered_size = current_size

                last_sample_time = now
                last_sample_size = current_size

            # A very fast download may already be complete before the first poll.
            if last_rendered_size < 0:
                current_size = sum(
                    p.stat().st_size for p in task_dir.rglob("*") if p.is_file()
                )
                elapsed = max(time.monotonic() - download_started, 0.001)
                speed_label = f"{pretty_size(current_size / elapsed)}/s" if current_size else "No file data observed"
                await send_progress(
                    status,
                    "⬇️ <b>Download finished</b>\\n"
                    f"<blockquote>{html.escape(title[:180])}</blockquote>\\n"
                    f"📦 {pretty_size(current_size)} downloaded\\n"
                    f"⚡ Average speed: {speed_label}",
                )

            download_result = await download_task
            if download_result is False or getattr(download_result, "status", None) == "failed":
                raise RuntimeError(f"{get_spec(provider).name} API reported that the download failed.")

        try:
            output = await find_valid_video(task_dir)
        except Exception as first_error:
            if provider != "eporner":
                raise
            await send_progress(
                status,
                "⚠️ <b>Downloaded file failed validation</b>\n"
                f"<blockquote>{html.escape(title[:180])}</blockquote>\n"
                "🔁 Removing the incomplete file and retrying the download once…",
            )
            # The API can occasionally leave a truncated MP4 behind. Clear every
            # artifact before retrying so the second attempt cannot reuse it.
            for child in task_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            retry_result = await video.download(configuration=config, mode=EpornerEncoding.mp4_h264)
            if retry_result is False or getattr(retry_result, "status", None) == "failed":
                raise RuntimeError("ePorner API reported that the retry download failed.") from first_error
            output = await find_valid_video(task_dir)

        actual_title = getattr(video, "title", None) or title
        size = output.stat().st_size

        active_downloads[key]["status"] = "waiting_upload"
        await send_progress(
            status,
            "✅ <b>Download complete</b>\n"
            f"<blockquote>{html.escape(actual_title[:180])}</blockquote>\n"
            f"📦 {pretty_size(size)}\n"
            "⏳ Waiting for an upload slot…",
        )

        async with upload_slots:
            active_downloads[key]["status"] = "uploading"
            await upload_file(output, actual_title, status, key)

        await send_progress(
            status,
            "✨ <b>Transfer complete</b>\n"
            f"<blockquote>{html.escape(actual_title[:180])}</blockquote>\n"
            f"📦 {pretty_size(size)}\n"
            "📤 Sent to your Telegram chat.",
        )
    except asyncio.CancelledError:
        await send_progress(status, "⏹️ <b>Task cancelled.</b>")
        raise
    except Exception as exc:
        log.exception("Task failed: %s", key)
        await send_progress(status, f"❌ <b>Task failed</b>\n<code>{html.escape(str(exc)[:700])}</code>")
    finally:
        if download_task is not None and not download_task.done():
            download_task.cancel()
            await asyncio.gather(download_task, return_exceptions=True)
        active_downloads.pop(key, None)
        task_handles.pop(key, None)
        shutil.rmtree(task_dir, ignore_errors=True)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return await deny(message)
    await state.clear()
    await message.answer(
        "╭━━━ <b>BEGG</b> ━━━╮\n"
        "│  <i>Your personal video assistant</i>\n"
        "╰━━━━━━━━━━━━━━╯\n\n"
        "Welcome back! Choose an action below.\n\n"
        "🔎 <b>Search</b> videos from the selected source or paste a video link\n"
        f"🔁 <b>Source</b> choose from {len(PROVIDERS)} video APIs\n"
        "📊 <b>Tasks</b> monitor and control transfers\\n"
        "🖥️ <b>System</b> inspect runtime resources",
        reply_markup=main_menu(ACTIVE_PROVIDER),
    )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return await deny(message)
    await state.clear()
    await message.answer("Cancelled. You're back at the main menu.", reply_markup=main_menu(ACTIVE_PROVIDER))


@router.callback_query(F.data == "menu:search")
async def search_button(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    await state.set_state(SearchFlow.waiting_query)
    await call.message.edit_text(
        f"🔎 <b>Find a video</b>\nSource: <b>{get_spec(ACTIVE_PROVIDER).name}</b>\nSend a search phrase or paste a video URL.",
        reply_markup=InlineKeyboardBuilder().button(text="✕ Cancel", callback_data="menu:home").as_markup(),
    )
    await call.answer()


@router.callback_query(F.data == "menu:provider")
async def provider_button(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    await call.message.edit_text(
        f"🔁 <b>Choose video source</b>\nActive: <b>{get_spec(ACTIVE_PROVIDER).name}</b>\n\nSelect a provider. Pornhub is excluded.",
        reply_markup=provider_picker_keyboard(ACTIVE_PROVIDER, 0),
    )
    await call.answer()


@router.callback_query(F.data == "provider:noop")
async def provider_noop(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    await call.answer()


@router.callback_query(F.data.startswith("provider:page:"))
async def provider_page(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    page = max(0, int(call.data.rsplit(":", 1)[1]))
    await call.message.edit_reply_markup(reply_markup=provider_picker_keyboard(ACTIVE_PROVIDER, page))
    await call.answer()


@router.callback_query(F.data.startswith("provider:set:"))
async def provider_set(call: CallbackQuery):
    global ACTIVE_PROVIDER
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    key = call.data.split(":", 2)[2]
    if key not in PROVIDERS:
        return await call.answer("Unknown provider", show_alert=True)
    ACTIVE_PROVIDER = key
    source_name = get_spec(key).name
    await call.message.edit_text(
        f"╭━━━ <b>BEGG</b> ━━━╮\n│  <i>Active source: {source_name}</i>\n╰━━━━━━━━━━━━━━╯\n\nNew searches use this provider. Direct links are detected automatically.",
        reply_markup=main_menu(ACTIVE_PROVIDER),
    )
    await call.answer(f"Source changed to {source_name}")


@router.callback_query(F.data == "menu:home")
async def home_button(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    await state.clear()
    await call.message.edit_text("╭━━━ <b>BEGG</b> ━━━╮\n│  <i>Personal media assistant</i>\n╰━━━━━━━━━━━━━━╯\nChoose an action:", reply_markup=main_menu(ACTIVE_PROVIDER))
    await call.answer()


def task_monitor_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for key, item in list(active_downloads.items())[:8]:
        short_id = key.rsplit(":", 1)[-1]
        label = {"queued": "🕓", "downloading": "⬇️", "waiting_upload": "📦", "uploading": "📤"}.get(item.get("status"), "•")
        kb.button(text=f"⏹ Stop {label} #{short_id[-5:]}", callback_data=f"task:cancel:{short_id}")
    if active_downloads:
        kb.button(text="⏹ Cancel all tasks", callback_data="tasks:cancel_all")
    kb.button(text="🔄 Refresh", callback_data="tasks:refresh")
    kb.button(text="🏠 Main menu", callback_data="menu:home")
    kb.adjust(1)
    return kb


def build_task_monitor_text() -> str:
    if not active_downloads:
        return "📊 <b>Task monitor</b>\n\n✨ No active tasks right now."
    icons = {"queued": "🕓", "downloading": "⬇️", "waiting_upload": "📦", "uploading": "📤"}
    rows = ["📊 <b>Task monitor</b>", ""]
    for key, item in active_downloads.items():
        short_id = key.rsplit(":", 1)[-1]
        detail = f"   <code>#{short_id[-7:]}</code> · {item.get('status', 'unknown').replace('_', ' ')}"
        current = int(item.get("current") or 0)
        total = int(item.get("total") or 0)
        speed = float(item.get("speed") or 0)
        if item.get("status") == "uploading" and total:
            detail += f"\n   {float(item.get('percent') or 0):.1f}% · {pretty_size(current)} / {pretty_size(total)} · {pretty_size(speed)}/s"
        elif item.get("status") == "downloading" and current:
            detail += f"\n   {pretty_size(current)} downloaded · {pretty_size(speed)}/s"
        rows.append(f"{icons.get(item.get('status'), '•')} <b>{html.escape(item.get('title', 'Untitled')[:65])}</b>\n{detail}")
    rows.append("")
    rows.append(f"Active: <b>{len(active_downloads)}</b> · Download slots: <b>{settings.download_concurrency}</b> · Upload slots: <b>{settings.upload_concurrency}</b>")
    return "\n".join(rows)


@router.callback_query(F.data.in_({"menu:tasks", "tasks:refresh"}))
async def tasks_button(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    await call.message.edit_text(build_task_monitor_text(), reply_markup=task_monitor_keyboard().as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("task:cancel:"))
async def cancel_one_task(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    short_id = call.data.rsplit(":", 1)[-1]
    key = f"{call.message.chat.id}:{short_id}"
    task = task_handles.get(key)
    if not task or task.done():
        return await call.answer("Task is no longer active.", show_alert=True)
    task.cancel()
    await call.answer("Cancellation requested.")
    await call.message.edit_text(build_task_monitor_text(), reply_markup=task_monitor_keyboard().as_markup())


@router.callback_query(F.data == "tasks:cancel_all")
async def cancel_all_tasks(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    tasks = [task for task in task_handles.values() if not task.done()]
    for task in tasks:
        task.cancel()
    await call.answer(f"Cancellation requested for {len(tasks)} task(s).")
    await call.message.edit_text(build_task_monitor_text(), reply_markup=task_monitor_keyboard().as_markup())


@router.callback_query(F.data == "menu:system")
async def system_monitor_button(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    disk = shutil.disk_usage(Path(settings.download_dir).resolve().parent)
    memory_text = "Unavailable"
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, raw = line.split(":", 1)
            values[name] = int(raw.strip().split()[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        if total:
            memory_text = f"{pretty_size(total - available)} / {pretty_size(total)} used"
    except (OSError, ValueError, IndexError):
        pass
    uptime = int(time.monotonic() - PROCESS_STARTED)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    load = "Unavailable"
    try:
        load = " · ".join(f"{value:.2f}" for value in os.getloadavg())
    except (AttributeError, OSError):
        pass
    text = (
        "🖥️ <b>BEGG system monitor</b>\n\n"
        f"⏱ Runtime: <b>{hours:02d}:{minutes:02d}:{seconds:02d}</b>\n"
        f"🧠 Memory: <b>{memory_text}</b>\n"
        f"💾 Disk free: <b>{pretty_size(disk.free)}</b> / {pretty_size(disk.total)}\n"
        f"⚙️ CPU cores: <b>{os.cpu_count() or 'Unknown'}</b>\n"
        f"📈 Load average: <b>{load}</b>\n"
        f"📊 Active tasks: <b>{len(active_downloads)}</b>\n"
        f"🔌 Providers configured: <b>{len(PROVIDERS)}</b>"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Refresh", callback_data="menu:system")
    kb.button(text="📊 Tasks", callback_data="menu:tasks")
    kb.button(text="🏠 Main menu", callback_data="menu:home")
    kb.adjust(2, 1)
    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()


def build_settings_text() -> str:
    return (
        "⚙️ <b>BEGG runtime settings</b>\n\n"
        f"⬇️ Download slots: <b>{settings.download_concurrency}</b> / 5\n"
        f"📤 Upload slots: <b>{settings.upload_concurrency}</b> / 2\n"
        f"🎞 Quality: <b>{html.escape(settings.video_quality)}</b>\n"
        f"🔄 Progress refresh: <b>{settings.update_interval}s</b>\n\n"
        "Changes apply to this runtime only. Slot changes are locked while tasks are active."
    )


def settings_markup():
    return settings_keyboard(
        settings.video_quality,
        settings.download_concurrency,
        settings.upload_concurrency,
        settings.update_interval,
    )


@router.callback_query(F.data == "menu:settings")
async def settings_button(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    await call.message.edit_text(build_settings_text(), reply_markup=settings_markup())
    await call.answer()


@router.callback_query(F.data.startswith("settings:"))
async def update_runtime_setting(call: CallbackQuery):
    global download_slots, upload_slots
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)

    try:
        _, setting_name, raw_value = call.data.split(":", 2)
        value = int(raw_value) if setting_name in {"downloads", "uploads", "interval"} else raw_value
    except (ValueError, TypeError):
        return await call.answer("Invalid setting.", show_alert=True)

    if setting_name == "quality":
        if value not in {"best", "half", "worst"}:
            return await call.answer("Unsupported quality preset.", show_alert=True)
        settings.video_quality = value
    elif setting_name in {"downloads", "uploads"}:
        if active_downloads:
            return await call.answer("Finish or cancel active tasks before changing concurrency.", show_alert=True)
        if setting_name == "downloads":
            if not 1 <= value <= 5:
                return await call.answer("Download slots must be 1–5.", show_alert=True)
            settings.download_concurrency = value
            download_slots = asyncio.Semaphore(value)
        else:
            if not 1 <= value <= 2:
                return await call.answer("Upload slots must be 1–2.", show_alert=True)
            settings.upload_concurrency = value
            upload_slots = asyncio.Semaphore(value)
    elif setting_name == "interval":
        if value not in {2, 3, 5, 10}:
            return await call.answer("Choose a listed refresh interval.", show_alert=True)
        settings.update_interval = value
    else:
        return await call.answer("Unknown setting.", show_alert=True)

    await call.message.edit_text(build_settings_text(), reply_markup=settings_markup())
    await call.answer("Setting updated for this runtime.")


@router.callback_query(F.data.in_({"menu:health", "health:refresh"}))
async def provider_health_button(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    await call.answer("Checking installed provider APIs…")
    await call.message.edit_text("🩺 <b>Provider health</b>\n\nChecking installed packages and API methods…")
    results = await check_all_providers()
    icons = {"ready": "🟢", "missing": "🔴", "incomplete": "🟠"}
    lines = ["🩺 <b>Provider health report</b>", ""]
    for item in results:
        lines.append(f"{icons.get(item.status, '⚪')} <b>{html.escape(item.name)}</b> — {item.status}")
        lines.append(f"   <i>{html.escape(item.detail)}</i>")
    ready = sum(item.status == "ready" for item in results)
    missing = sum(item.status == "missing" for item in results)
    incomplete = sum(item.status == "incomplete" for item in results)
    lines.extend(["", f"🟢 Ready: <b>{ready}</b>  🔴 Missing: <b>{missing}</b>  🟠 Incomplete: <b>{incomplete}</b>",
                  "", "<i>Package/API capability check only; this does not verify live website availability.</i>"])
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Run check again", callback_data="health:refresh")
    kb.button(text="⚙️ Settings", callback_data="menu:settings")
    kb.button(text="🏠 Main menu", callback_data="menu:home")
    kb.adjust(1)
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())


async def render_gallery(message: Message, item: dict, index: int, total: int) -> Message:
    details = []
    if item.get("creator"):
        details.append(f"👤 {html.escape(str(item['creator'])[:80])}")
    if item.get("duration"):
        details.append(f"⏱ {html.escape(str(item['duration'])[:20])}")
    if item.get("views"):
        details.append(f"👁 {html.escape(str(item['views'])[:24])}")
    if item.get("uploaded"):
        details.append(f"🕒 {html.escape(str(item['uploaded'])[:24])}")
    caption = (
        f"🎬 <b>{html.escape(item['title'][:180])}</b>\n"
        + ("\n".join(details) + "\n" if details else "")
        + f"🆔 <code>{html.escape(item['video_id'])}</code>\n"
        + f"📍 <b>Result {index + 1} of {total}</b>"
    )
    provider = item.get("provider", "eporner")
    markup = gallery_keyboard(index, total, item["url"], provider)
    has_photo = bool(message.photo)
    has_thumb = bool(item.get("thumbnail"))

    if has_photo and has_thumb:
        await message.edit_media(
            InputMediaPhoto(media=item["thumbnail"], caption=caption),
            reply_markup=markup,
        )
        return message
    if not has_photo and not has_thumb:
        await message.edit_text(caption, reply_markup=markup)
        return message

    # Telegram cannot change a text message into a photo (or vice versa) in place.
    chat_id = message.chat.id
    await message.delete()
    if has_thumb:
        return await message.bot.send_photo(
            chat_id=chat_id, photo=item["thumbnail"], caption=caption, reply_markup=markup
        )
    return await message.bot.send_message(chat_id=chat_id, text=caption, reply_markup=markup)


@router.callback_query(F.data.in_({"gallery:prev", "gallery:next"}))
async def gallery_navigate(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    data = await state.get_data()
    results = data.get("search_results", [])
    if not results:
        return await call.answer("Search results expired. Please search again.", show_alert=True)
    index = int(data.get("search_index", 0))
    next_index = index - 1 if call.data == "gallery:prev" else index + 1
    if next_index < 0 or next_index >= len(results):
        return await call.answer("No more results in that direction.")
    await state.update_data(search_index=next_index)
    await render_gallery(call.message, results[next_index], next_index, len(results))
    await call.answer()


@router.callback_query(F.data == "gallery:page")
async def gallery_page_info(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    await call.answer("Use Previous and Next to browse results.")


@router.callback_query(F.data == "gallery:download")
async def gallery_download(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Not authorized", show_alert=True)
    data = await state.get_data()
    results = data.get("search_results", [])
    if not results:
        return await call.answer("Search results expired. Please search again.", show_alert=True)
    index = int(data.get("search_index", 0))
    item = results[index]
    await call.answer("Added to download pipeline")
    await run_download(item["url"], item["title"], call.message, item.get("provider", "eporner"))


@router.message(SearchFlow.waiting_query)
async def process_search(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id if message.from_user else None):
        return await deny(message)
    query = (message.text or "").strip()
    if not query:
        return await message.answer("Please send a search phrase or a supported provider URL.")
    await state.clear()

    eporner_url = clean_eporner_video_url(query)
    detected_provider = provider_for_url(query)
    if eporner_url:
        await run_download(eporner_url, "ePorner video", message, "eporner")
        return
    if detected_provider and detected_provider != "eporner":
        await run_download(query, f"{get_spec(detected_provider).name} video", message, detected_provider)
        return

    if is_eporner_category_url(query):
        provider = "eporner"
    else:
        provider = ACTIVE_PROVIDER
    provider_name = get_spec(provider).name
    wait = await message.answer(f"🔎 <b>Searching {provider_name}…</b>\n<code>{html.escape(query[:100])}</code>")
    try:
        if provider == "eporner":
            items = await fetch_eporner_category_videos(query, settings.search_limit) if is_eporner_category_url(query) else await search_eporner_videos(query, settings.search_limit)
        else:
            if "://" in query and provider_for_url(query) == provider:
                raise ValueError("Paste a video URL to download it; send keywords here to search.")
            items = await search_generic_videos(provider, query, settings.search_limit)

        serializable = [
            {
                "title": item_field(item, "title"),
                "url": item_field(item, "url"),
                "video_id": item_field(item, "video_id"),
                "thumbnail": item_field(item, "thumbnail"),
                "creator": item_field(item, "creator"),
                "creator_url": item_field(item, "creator_url"),
                "duration": item_field(item, "duration"),
                "views": item_field(item, "views"),
                "uploaded": item_field(item, "uploaded"),
                "provider": provider,
            }
            for item in items
            if item_field(item, "url") and item_field(item, "title")
        ]
        results = serializable
    except Exception as exc:
        log.exception("Search failed")
        await wait.edit_text(f"⚠️ Search failed: <code>{html.escape(str(exc)[:500])}</code>", reply_markup=main_menu(ACTIVE_PROVIDER))
        return

    if not results:
        await wait.edit_text(
            f"No {provider_name} results found. Try another phrase or switch the source from the main menu.",
            reply_markup=main_menu(ACTIVE_PROVIDER),
        )
        return

    await state.update_data(search_results=serializable, search_index=0)
    first = serializable[0]
    details = []
    if first.get("creator"):
        details.append(f"👤 {html.escape(str(first['creator'])[:80])}")
    if first.get("duration"):
        details.append(f"⏱ {html.escape(str(first['duration'])[:20])}")
    if first.get("views"):
        details.append(f"👁 {html.escape(str(first['views'])[:24])}")
    if first.get("uploaded"):
        details.append(f"🕒 {html.escape(str(first['uploaded'])[:24])}")
    caption = (
        f"🎬 <b>{html.escape(first['title'][:180])}</b>\n"
        + ("\n".join(details) + "\n" if details else "")
        + f"🆔 <code>{html.escape(first['video_id'])}</code>\n"
        + f"📍 <b>Result 1 of {len(serializable)}</b>"
    )
    markup = gallery_keyboard(0, len(serializable), first["url"], provider)
    if first.get("thumbnail"):
        try:
            await wait.delete()
            await message.answer_photo(first["thumbnail"], caption=caption, reply_markup=markup)
        except Exception:
            log.exception("Could not display search thumbnail")
            await message.answer(caption + "\n<i>Thumbnail unavailable</i>", reply_markup=markup)
    else:
        await wait.edit_text(caption + "\n<i>Thumbnail unavailable</i>", reply_markup=markup)


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
