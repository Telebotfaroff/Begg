# BEGG — Personal Beeg Telegram Bot

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Telebotfaroff/Begg/blob/main/BEGG_Colab.ipynb)

A private, admin-only Telegram bot with a polished inline interface, Beeg metadata/download integration, and WZGram MTProto uploads.

## Features

- Admin ID allowlist; all other users are rejected.
- Search results shown as a thumbnail gallery with Previous/Next navigation, result counter, Beeg link, and one-tap download.
- Aiogram 3 Bot API for the interface.
- Official Beeg API for video metadata (`title`, `duration`, `author`, `tags`, `publish_date`, and `thumbnail`) and HLS downloads.
- WZGram bot-authenticated MTProto client for uploads (no personal Telegram session).
- 5 concurrent downloads and 2 concurrent uploads.
- Download and upload status messages with size, speed and ETA where available.
- Telegram-ready MP4 uploads: H.264 video, AAC audio when present, MP4 faststart, real duration/dimensions, generated JPEG thumbnail, and streaming enabled.
- In-memory task tracking; no database.
- Automatic temporary-file cleanup.

## Setup

Python 3.12 or newer is recommended.

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


## Interface

The bot uses compact inline navigation, clear task states, separate download/upload progress cards, and a minimal premium visual style using Telegram's native message formatting.

## Notes

- The official Beeg API provides video lookup through `Client.get_video(url)`. HLS configuration is imported from `base_api` and passed as `video.download(configuration=config)`. Keyword search is not part of the documented API, so it remains isolated in `services/beeg.py` as a separate HTML search adapter; validate the current search-page response during the first live run.
- HLS downloads do not always expose a reliable total size before completion. During that phase the bot reports bytes downloaded and live speed; final size is shown after completion.
- Before upload, FFmpeg/ffprobe prepares an MP4 with `+faststart`. Compatible H.264/AAC files are remuxed without re-encoding; other codecs/containers are normalized to H.264/AAC. The uploader sends the measured duration, width, height, a generated JPEG thumbnail, and `supports_streaming=True`. This preparation requires `ffmpeg` and `ffprobe` to be available in the runtime (both are normally available in Google Colab).
- The WZGram uploader authenticates with the bot token and Telegram API credentials; no phone number, OTP, 2FA, or user session is used.
