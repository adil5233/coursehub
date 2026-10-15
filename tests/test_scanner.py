import pytest

from app.config import display_title, format_ms, natural_sort_key
from app.scanner.drive_scanner import parse_folder_id
from app.scanner.local_scanner import scan_local
from app.scanner.telegram_scanner import section_from_caption, title_from_message


def test_natural_sort():
    assert sorted(["10 end", "2 basics", "1 intro"], key=natural_sort_key) == ["1 intro", "2 basics", "10 end"]


def test_format_ms():
    assert format_ms(0) == "0:00"
    assert format_ms(65_000) == "1:05"
    assert format_ms(3_725_000) == "1:02:05"


def test_display_title():
    assert display_title("1-2 Which Language.mkv") == "1-2 Which Language"
    assert display_title("2.6 Second Largest") == "2.6 Second Largest"
    assert display_title(".mp4") == ".mp4"


def test_scan_local_sections_order_and_filters(tmp_path):
    files = [
        "welcome.mp4",
        "1. Intro/1 hello.webm",
        "1. Intro/1 hello.srt",
        "1. Intro/2 setup.mkv",
        "1. Intro/10 wrap.avi",
        "1. Intro/notes.pdf",
        "2. Basics/a.MOV",
        "10. End/z.flv",
        "10. End/Deep/inner.m4v",
    ]
    for rel in files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    videos = scan_local(str(tmp_path))

    assert [v.ref for v in videos] == [
        "welcome.mp4",
        "1. Intro/1 hello.webm",
        "1. Intro/2 setup.mkv",
        "1. Intro/10 wrap.avi",
        "2. Basics/a.MOV",
        "10. End/z.flv",
        "10. End/Deep/inner.m4v",
    ]
    assert videos[0].section == ""
    assert videos[1].section == "1. Intro"
    assert videos[-1].section == "10. End / Deep"
    assert videos[1].title == "1 hello"
    assert videos[1].subtitle_ref == "1. Intro/1 hello.srt"
    assert videos[2].subtitle_ref is None


def test_scan_local_missing_folder(tmp_path):
    with pytest.raises(FileNotFoundError):
        scan_local(str(tmp_path / "nope"))


def test_parse_folder_id():
    assert parse_folder_id("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp?usp=sharing") == "1AbCdEfGhIjKlMnOp"
    assert parse_folder_id("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp") == "1AbCdEfGhIjKlMnOp"
    assert parse_folder_id("1AbCdEfGhIjKlMnOp") == "1AbCdEfGhIjKlMnOp"
    assert parse_folder_id("root") == "root"
    assert parse_folder_id("x' or name contains 'a") is None


def test_telegram_helpers():
    assert section_from_caption("Lecture 1 #Module_1") == "Module 1"
    assert section_from_caption("no tags") == ""
    assert title_from_message("#Module1\nIntro to Python\nmore text", "a.mp4", 5) == "Intro to Python"
    assert title_from_message("", "lesson 3.mkv", 5) == "lesson 3"
    assert title_from_message(None, None, 7) == "Video 7"
