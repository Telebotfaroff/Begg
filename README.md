# BEGG — Multi-source Personal Telegram Video Bot

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Telebotfaroff/Begg/blob/main/BEGG_Colab.ipynb)

A private, admin-only Telegram bot using the video API projects published by [EchterAlsFake](https://github.com/EchterAlsFake). **Pornhub is intentionally excluded.** ePorner is the default source.

## Providers

The source picker includes these providers:

- ePorner
- Beeg
- XVideos
- XNXX
- xHamster
- YouPorn
- XFreeHD
- HQPorner
- MissAV
- PornTrex
- SpankBang
- RedTube
- Tube8
- Thumbzilla
- TNAFlix
- Bellesa
- Empflix
- PornDoe
- PornGo
- PornHat
- Sex.com
- YouJizz
- RedGIFs
- Porn.com

The integrations use the corresponding upstream Python packages. Search and download capabilities vary by API; providers without a compatible search or download method report a clear provider-specific error rather than silently switching sources.

## Features

- Admin ID allowlist; other users are rejected.
- Paginated source picker with ePorner selected by default.
- Direct video URLs are routed to the matching provider automatically.
- Search results use one shared gallery with thumbnail, metadata, Previous/Next and one-tap download.
- Each gallery item retains its original provider.
- Shared download validation with FFprobe; invalid or incomplete output is rejected before upload.
- ePorner download result is checked and an invalid download is retried once.
- Aiogram 3 Bot API for the interface.
- WZGram bot-authenticated MTProto uploads; no personal Telegram session.
- Up to 5 concurrent downloads and 2 concurrent uploads.
- Progress messages show file size, speed and ETA where available.
- Telegram-ready MP4 preparation, metadata and JPEG thumbnail.
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

Set these values in `.env`:

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token |
| `API_ID` | Telegram API ID |
| `API_HASH` | Telegram API hash |
| `ADMIN_ID` | Your numeric Telegram user ID |
| `DOWNLOAD_CONCURRENCY` | Download slots (maximum 5) |
| `UPLOAD_CONCURRENCY` | Upload slots (maximum 2) |
| `VIDEO_QUALITY` | `best`, `half`, `worst`, or a supported resolution |

Run:

```bash
python bot.py
```

## Google Colab

Open the [BEGG Colab notebook](https://colab.research.google.com/github/Telebotfaroff/Begg/blob/main/BEGG_Colab.ipynb). Add `BOT_TOKEN`, `API_ID`, `API_HASH`, and `ADMIN_ID` under Colab Secrets, enable notebook access to secrets, and run the cells. The notebook installs all provider dependencies from `requirements.txt`. Keep the Colab runtime active while the bot is running.

## Architecture

- `services/providers.py`: provider registry, dynamic API clients, common metadata mapping, URL detection and generic download configuration.
- `services/eporner.py`: ePorner-specific URL parsing and search/category integration.
- `services/beeg.py` and `services/beeg_html.py`: Beeg-specific search and category handling.
- `ui/keyboards.py`: paginated provider selector and common gallery controls.
- `bot.py`: Telegram UI, download validation, media preparation and WZGram uploads.

Each provider is isolated by its own API package. The shared bot pipeline handles the resulting media consistently. Pornhub is not installed, listed or selectable.
