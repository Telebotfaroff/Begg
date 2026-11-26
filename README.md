# BEGG — Personal ePorner-first Telegram Bot

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Telebotfaroff/Begg/blob/main/BEGG_Colab.ipynb)

A private, admin-only Telegram bot with ePorner as its primary provider, Beeg as a selectable secondary provider, a polished inline gallery, and WZGram MTProto uploads.

## Features

- Admin ID allowlist; all other users are rejected.
- ePorner is the default source for keyword search, category browsing, metadata and direct video downloads.
- Switch providers from the main menu; Beeg remains available as a secondary source.
- No silent fallback: empty or failed ePorner searches stay on ePorner until you manually switch to Beeg.
- Thumbnail gallery with Previous/Next navigation, result counter, provider link, metadata and one-tap download.
- Beeg keyword search, category/creator page parsing, metadata and HLS downloads.
- Aiogram 3 Bot API for the interface.
- WZGram bot-authenticated MTProto client for uploads (no personal Telegram session).
- 5 concurrent downloads and 2 concurrent uploads.
- Download and upload status messages with size, speed and ETA where available.
- Telegram-ready MP4 uploads: H.264 video, AAC audio when present, MP4 faststart, measured duration/dimensions, generated JPEG thumbnail, and streaming enabled.
- In-memory task tracking; no database.
- Automatic temporary-file cleanup.

## Setup

Python 3.12 or newer is required by both provider API packages.

```bash
git clone https://github.com/Telebotfaroff/Begg.git
cd Begg
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Bot token from BotFather |
| `API_ID` | Telegram API ID |
| `API_HASH` | Telegram API hash |
| `ADMIN_ID` | Your numeric Telegram user ID |
| `DOWNLOAD_CONCURRENCY` | Maximum 5 |
| `UPLOAD_CONCURRENCY` | Maximum 2 |
| `VIDEO_QUALITY` | `best`, `half`, `worst`, or a supported resolution |

Run:

```bash
python bot.py
```

## Google Colab

Open the [BEGG Colab notebook](https://colab.research.google.com/github/Telebotfaroff/Begg/blob/main/BEGG_Colab.ipynb). Add `BOT_TOKEN`, `API_ID`, `API_HASH`, and `ADMIN_ID` under Colab Secrets, enable notebook access to secrets, and run the cells. Keep the Colab runtime active while the bot is running.

## Provider implementation

- ePorner integration is isolated in `services/eporner.py`. It uses the library's async `Client.search_videos(...)`, `Client.get_videos_by_category(...)`, `Client.get_video(...)`, and `DownloadConfigRAW` + H.264 download mode.
- Beeg integration remains isolated in `services/beeg.py` and `services/beeg_html.py`. It uses the Beeg API's HLS downloader.
- Provider selection is global for this single-admin personal bot. Each search result also stores its provider, so gallery navigation and downloads continue using the correct source.
- The ePorner library's API listing returns async scrape results; each result is unwrapped before the bot maps its metadata into the common gallery format.

## Transfer pipeline

Before upload, FFmpeg/ffprobe prepares an MP4 with `+faststart`. Compatible H.264/AAC files are remuxed without re-encoding; other codecs/containers are normalized to H.264/AAC. The uploader sends measured duration, width, height, a generated JPEG thumbnail, and `supports_streaming=True`. This requires `ffmpeg` and `ffprobe` to be available in the runtime (both are normally available in Google Colab).
