# CourseHub

A Udemy-style desktop app for Windows to watch video courses from your **local PC**, **Google Drive** or **Telegram channels**, with progress tracking and resume.

## Features

- **Three sources:** a local folder, a Google Drive folder, or a Telegram channel/group.
  - Subfolders, or `#hashtags` for Telegram, become sections.
- **Progress tracking:**
  - Each lecture is marked complete when it finishes, and course progress is shown on the library card.
  - On restart the app reopens the same course and lecture at the same second.
- **All video formats:** mp4, mkv, avi, mov, webm, flv, ts, wmv and more, played with VLC.
  - Subtitles (`.srt` / `.vtt` / `.ass`) next to a video are loaded automatically.
- **YouTube-style player:**
  - Overlay controls that auto-hide.
  - Click-to-seek, speed 0.5x–2x, volume/mute, theater mode and fullscreen.
- **Netflix-style "Up next":** a card appears 10 seconds before a lecture ends; press **Cancel** to stop the autoplay.
- **Notes** per lecture, in a panel you can hide.
- **Thumbnails** for courses and the playlist, cached on disk.
- **Streaming with cache:**
  - Drive and Telegram videos stream through a local proxy and support seeking.
  - Telegram chunks are kept in a disk cache (up to 2 GB), which can be cleared from **Settings**.

## Requirements

- Windows 10/11
- Python 3.11+ (developed on 3.14)
- [VLC media player](https://www.videolan.org/vlc/) **64-bit**, installed (the app uses `libvlc.dll`)

## Setup

```powershell
git clone https://github.com/adil5233/coursehub.git
cd coursehub
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

## Adding a course

Click **+ Add Course**, give it a name and pick a source.

| Source       | What you need                                                                                                                                                                                           |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Local folder | Choose the folder with the videos                                                                                                                                                                       |
| Google Drive | A Google Cloud OAuth client (`credentials.json`, Desktop app type, Drive API enabled). The app asks for it on first use, then you sign in once in the browser. Paste a folder link or browse your Drive |
| Telegram     | `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org), then log in with your phone number and code. Pick a channel/group you have joined                                              |

## Keyboard shortcuts

| Key     | Action                       |
| ------- | ---------------------------- |
| Space   | Play / pause                 |
| ← / →   | Seek -10s / +10s             |
| ↑ / ↓   | Volume +5 / -5               |
| M       | Mute                         |
| [ / ]   | Slower / faster              |
| F / Esc | Fullscreen / exit fullscreen |
| T       | Theater mode (hide playlist) |
| N / P   | Next / previous lecture      |

## Where data is stored

Everything is kept in `%APPDATA%\CourseHub`:

- `data.db`: courses, progress and notes (SQLite)
- `credentials.json`, `token.json`: Google Drive login
- `telegram.json`, `telegram.session`: Telegram login
- `cache/`: thumbnails, subtitles and Telegram video chunks

None of these are part of the repository. Keep the login files private.

## Project structure

```
main.py                 entry point
app/
  config.py             paths and constants
  db.py                 SQLite storage
  services.py           sources, streaming and cache
  stream_proxy.py       localhost HTTP proxy with Range support
  scanner/              local / Drive / Telegram scanners
  drive/                Google Drive auth
  telegram/             Telethon client and chunk cache
  player/vlc_widget.py  VLC player, overlay controls, Up next card
  ui/                   library, course view, dialogs, theme, thumbnails
tests/                  pytest tests
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```
