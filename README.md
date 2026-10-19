# CourseHub

A Udemy-style desktop app for Windows to watch video courses from your **local PC**, **Google Drive** or **Telegram channels**, with progress tracking and resume.

## Features

- **Three sources:** a local folder, a Google Drive folder, or a Telegram channel, group, bot or private chat.
  - Subfolders, or `#hashtags` for Telegram, become sections.
  - **Sync** button rescans the source for new or removed videos; progress is kept.
- **Progress tracking:**
  - Each lecture is marked complete when it finishes, and course progress is shown on the library card.
  - On restart the app reopens the same course and lecture at the same second.
- **All video formats:** mp4, mkv, avi, mov, webm, flv, ts, wmv and more, played with VLC.
  - Subtitles (`.srt` / `.vtt` / `.ass`) next to a video are loaded automatically.
- **YouTube-style player:**
  - Overlay controls that auto-hide.
  - Speed 0.5x–2x, volume/mute, theater mode and fullscreen.
  - Animated loading spinner while a video opens or buffers.
- **YouTube-style seek bar:**
  - Grows on hover, with a red scrubber and a lighter fill up to the hovered point.
  - Hover anywhere to see a preview frame and the time.
  - Shows which parts are already downloaded.
  - Click anywhere, even while the video is still loading.
- **Netflix-style "Up next":** a card appears 10 seconds before a lecture ends; press **Cancel** to stop the autoplay.
- **Notes** per lecture, in a panel you can hide.
- **Thumbnails** for courses and the playlist, cached on disk.
- **Streaming with cache:**
  - Drive and Telegram videos stream through a local proxy and support seeking.
  - Telegram chunks are kept in a disk cache (up to 2 GB), which can be cleared from **Settings**.
  - Downloads are prioritised: what the player needs now, then ~90 s of read-ahead, then background work.
    Seeking cancels read-ahead that is no longer needed.
  - The start of the next lecture is downloaded in the background, so it starts almost instantly.

## Study features

These need a free [Groq API key](https://console.groq.com/keys) (Settings → Transcripts) and the lecture's transcript.

To stay inside Groq's free limits:
- **Study pack**: steps, chapters and quiz questions come from one read of the transcript, made once per lecture.
- **Model fallback**: Groq's limits are per model, so when one model hits its limit the next one takes over
  (gpt-oss-120b → llama-3.3-70b → gpt-oss-20b → qwen3.8-27b → llama-3.1-8b-instant).
- **OpenRouter backup** (optional): add a free [OpenRouter key](https://openrouter.ai/keys) in Settings and its
  free models take over when every Groq model is at its limit (about 50 requests a day without credits).
  Transcripts still need Groq.

| Feature | What it does |
| --- | --- |
| Transcript | Groq Whisper subtitles and a searchable transcript; click a line to jump there |
| AI chapters | YouTube-style chapters on the seek bar |
| **Steps + Study mode** | The lecture cut into 3-5 minute steps, each with a one-line gist, up to 2 new words and a check question. In study mode the video pauses after every step: answer first, then see the answer and mark it (Samajh gaya / Thoda / Nahi → replay the step) |
| **Answer in your own words** | Type or 🎤 speak an answer; the AI scores it 0-10 and says what was right, what was missing and a better version |
| Quiz | MCQ, **Apne shabdon me** (own words) and **Interview** (simple English) modes; the score schedules the next revision (Revise today) |
| **Meri galtiyan** | Every wrong answer is logged with how many times it went wrong (Baar). From Baar 3 the AI explains it in a fresh way; save that as a note or jump to that moment |
| Ask | Questions about the lecture or the whole course, with clickable sources. **Abhi wala samjhao** explains the last ~90 seconds simply. Typing pauses the video; doubts are kept per lecture |
| AI revision note | QUESTIONS / ANSWERS / MERI GALTI / EK LINE ME + a mantra |
| Export | All notes as one Markdown file, or revision notes as one `review.py`-style file per lecture (course right-click menu) |
| Search | Every note and transcript in the library (Ctrl+K) |

## Requirements

- Windows 10/11 (Linux works too, see below)
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

On Linux: `sudo apt install vlc`, then `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` and
`QT_QPA_PLATFORM=xcb .venv/bin/python main.py` (`xcb` lets VLC draw inside the window under Wayland). Data goes to
`~/CourseHub`.

## Adding a course

Click **+ Add Course**, give it a name and pick a source.

| Source       | What you need                                                                                                                                                                                           |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Local folder | Choose the folder with the videos                                                                                                                                                                       |
| Google Drive | A Google Cloud OAuth client (`credentials.json`, Desktop app type, Drive API enabled). The app asks for it on first use, then you sign in once in the browser. Paste a folder link or browse your Drive |
| Telegram     | `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org), then log in with your phone number and code. Pick a channel, group, bot or chat (search box helps with many chats)             |

> Telegram limits download speed for non-Premium accounts (about 0.5 MB/s in our tests), so a lecture that was never opened can take 20-30 seconds to start. Cached and prefetched lectures start much faster.

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

Only one CourseHub window can run at a time (two copies would fight over the Telegram login file).

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
  player/vlc_widget.py  VLC player, overlay controls, seek bar, Up next card
  player/preview.py     seek bar hover previews
  transcribe/           Whisper transcripts, chapters, steps, quiz, grading, Ask, summary (Groq)
  notes_export.py       Markdown and review.py-style exports
  ui/                   library, course view, panels (steps, ask, notes), dialogs, theme
tests/                  pytest tests
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

## License

[MIT](LICENSE). Third-party libraries keep their own licenses (PySide6 and python-vlc/VLC are LGPL, Telethon is MIT, Google API clients are Apache 2.0).
