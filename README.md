# BEGG — Multi-source Personal Telegram Video Bot

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Telebotfaroff/Begg/blob/main/BEGG_Colab.ipynb)

A private, admin-only Telegram bot using the video API projects published by [EchterAlsFake](https://github.com/EchterAlsFake). **Pornhub is intentionally excluded.** ePorner is the default source.


## Scraper Center (BEGG 2.1)

The Telegram main menu includes **Scraper Center**, a metadata-only workflow separate from video downloads.

- **Homepage / listings:** provider-specific latest/popular feeds where the API exposes a listing method.
- **Categories:** list categories where supported, then scrape a category URL or exact category name.
- **Bulk scrape:** search multiple pages with a configurable start page, end page and result cap.
- **Profile / channel:** scrape supported playlists, channels, creators, models or profile URLs.
- **Exports:** download the current in-memory result set as JSON or CSV.
- **Deduplication:** duplicate video URLs are removed before results are shown.
- **No database:** results live in Telegram FSM memory and disappear when the runtime/session state is cleared.
- **No media transfer:** Scraper Center only collects metadata; it does not download or upload videos.

Provider support is method-specific. Not every API exposes homepage listings, category indexes or profile methods. Bulk search is available wherever the provider exposes a compatible search method. Page counts and result limits are bounded by the bot UI; upstream APIs may impose additional pagination limits.

### Current API coverage

- Bulk search is wired through each provider's `search_videos` or `search` method, with pagination parameters passed only when the installed API exposes them.
- Known homepage/listing adapters are currently available for **HQPorner, Bellesa, PornHat and YouJizz**. The feed type selector offers latest, popular and trending; each provider maps these to its own sort/listing enum when available.
- Category indexes are provider-dependent. Known category-list methods are available on **HQPorner, Bellesa, PornHat and YouJizz**. ePorner can scrape a category URL directly even without a category index.
- Profile/playlist scraping is discovered from supported API methods such as playlist, channel, site, model, creator, user and pornstar methods. A provider may not implement every profile type.
- A requested non-first bulk page is rejected when the provider method does not expose a start-page parameter, rather than silently scraping the wrong pages.

## Providers

The source picker includes 15 providers not flagged as API failures in the diagnostic report:

- ePorner
- XVideos
- XNXX
- xHamster
- YouPorn
- XFreeHD
- HQPorner
- RedTube
- Tube8
- Thumbzilla
- TNAFlix
- Bellesa
- Empflix
- PornHat
- YouJizz

The integrations use the corresponding upstream Python packages. Providers that failed the API smoke test have been removed from the source picker and dependency list.

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
- In-memory task tracking; no database or persistent history.
- Task monitor with per-task cancellation and cancel-all controls.
- System monitor with runtime, memory, disk, CPU-core and load information.
- Interactive in-memory settings for quality, download/upload concurrency and progress refresh interval.
- Provider health dashboard checks installed API packages, client initialization and required search/video methods.
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
- `ui/keyboards.py`: paginated provider selector, interactive settings and common gallery controls.
- `services/provider_health.py`: local provider package and API capability diagnostics (not a live website uptime test).
- `bot.py`: Telegram UI, in-memory task manager, system monitor, download validation, media preparation and WZGram uploads.

Each provider is isolated by its own API package. The shared bot pipeline handles the resulting media consistently. Pornhub is not installed, listed or selectable.
