# BEGG — Personal Beeg Telegram Bot

A private, admin-only Telegram bot with a polished inline interface, Beeg metadata/download integration, and WZGram MTProto uploads.

## Features

- Admin ID allowlist; all other users are rejected.
- Aiogram 3 Bot API for the interface.
- Beeg API for video metadata and HLS downloads.
- WZGram bot-authenticated MTProto client for uploads (no personal Telegram session).
- 5 concurrent downloads and 2 concurrent uploads.
- Download and upload status messages with size, speed and ETA where available.
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

Open a new Colab notebook, clone the repository, install requirements, set the four required environment variables, then run `python bot.py`. Keep the Colab runtime active while the bot is running.

```python
!git clone https://github.com/Telebotfaroff/Begg.git
%cd Begg
!pip install -r requirements.txt
import os
os.environ["BOT_TOKEN"] = "YOUR_BOT_TOKEN"
os.environ["API_ID"] = "YOUR_API_ID"
os.environ["API_HASH"] = "YOUR_API_HASH"
os.environ["ADMIN_ID"] = "YOUR_TELEGRAM_USER_ID"
!python bot.py
```

## Interface

The bot uses compact inline navigation, clear task states, separate download/upload progress cards, and a minimal dark-themed visual language through Telegram's native message formatting.

## Notes

- The Beeg metadata library provides video lookup by URL. Keyword search is isolated in `services/beeg.py` as an HTML search adapter because keyword search is not part of the library's documented `Client` API.
- HLS downloads do not always expose a reliable total size before completion. During that phase the bot reports bytes downloaded and live speed; final size is shown after completion.
- The WZGram uploader authenticates with the bot token and Telegram API credentials; no phone number, OTP, 2FA, or user session is used.
