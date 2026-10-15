from app.config import format_bytes
from app.services import empty_folder, folder_size


def test_folder_size_and_empty(tmp_path):
    (tmp_path / "tg" / "123_45").mkdir(parents=True)
    (tmp_path / "tg" / "123_45" / "0.bin").write_bytes(b"x" * 1000)
    (tmp_path / "drive_abc.srt").write_bytes(b"y" * 24)

    assert folder_size(tmp_path) == 1024

    empty_folder(tmp_path)
    assert tmp_path.is_dir()
    assert list(tmp_path.iterdir()) == []
    assert folder_size(tmp_path) == 0


def test_empty_missing_folder_is_noop(tmp_path):
    empty_folder(tmp_path / "missing")


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(5 * 1024 ** 2) == "5.0 MB"
    assert format_bytes(3 * 1024 ** 4) == "3072.0 GB"
