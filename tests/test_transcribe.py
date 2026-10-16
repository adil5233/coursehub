import io
import math

import pytest

av = pytest.importorskip("av")

from app.transcribe import Segment, load_parts, merge_chunks, save_parts, srt_time, to_srt  # noqa: E402
from app.transcribe import load_transcript, save_transcript, split_segments  # noqa: E402
from app.transcribe import groq_client  # noqa: E402
from app.transcribe.audio import iter_chunks  # noqa: E402


def make_tone(path, seconds: float, rate: int = 44_100) -> None:
    """A short stereo 440 Hz tone, standing in for a lecture's audio track."""
    with av.open(str(path), "w") as out:
        stream = out.add_stream("pcm_s16le", rate=rate, layout="stereo")
        samples_per_frame = 1024
        total = int(seconds * rate)
        for start in range(0, total, samples_per_frame):
            n = min(samples_per_frame, total - start)
            frame = av.AudioFrame(format="s16", layout="stereo", samples=n)
            values = b"".join(
                int(8000 * math.sin(2 * math.pi * 440 * (start + i) / rate)).to_bytes(2, "little", signed=True) * 2
                for i in range(n)
            )
            frame.planes[0].update(values.ljust(frame.planes[0].buffer_size, b"\0"))
            frame.sample_rate = rate
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


def mp3_seconds(data: bytes) -> float:
    with av.open(io.BytesIO(data)) as container:
        stream = container.streams.audio[0]
        return sum(frame.samples for frame in container.decode(stream)) / stream.rate


def test_srt_format():
    assert srt_time(3725.5) == "01:02:05,500"
    srt = to_srt([Segment(0, 1.25, "hello"), Segment(1.25, 3, "world")])
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,250\nhello\n")
    assert "2\n00:00:01,250 --> 00:00:03,000\nworld" in srt


def test_long_subtitle_lines_become_two_balanced_rows():
    from app.transcribe import subtitle_rows

    assert subtitle_rows("short line") == "short line"
    rows = subtitle_rows("Okay, first day complete beginners guys, you might get a bit overwhelmed because you")
    assert rows == "Okay, first day complete beginners guys,\nyou might get a bit overwhelmed because you"
    assert "\nyou might get" in to_srt([Segment(0, 5, rows.replace("\n", " "))])


def test_merge_offsets_times_and_sorts_chunks():
    first = (0.0, [Segment(0, 5, "a"), Segment(598, 601, "b")])
    second = (600.0, [Segment(0.5, 1.5, "c"), Segment(1.5, 4, "d")])
    merged = merge_chunks([second, first])
    assert [(s.start, s.text) for s in merged] == [(0, "a"), (598, "b"), (600.5, "c"), (601.5, "d")]


def test_merge_drops_segments_that_start_in_the_overlap():
    # chunk 0 audio runs until 11 s; whatever starts after 10 s is the next chunk's job
    first = (0.0, [Segment(9.5, 10.6, "spans the boundary"), Segment(10.2, 10.9, "overlap words")])
    second = (10.0, [Segment(0.2, 0.9, "overlap words"), Segment(0.9, 2, "next")])
    merged = merge_chunks([first, second])
    assert [s.text for s in merged] == ["spans the boundary", "overlap words", "next"]


def test_parts_round_trip(tmp_path, monkeypatch):
    import app.transcribe as transcribe

    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    save_parts(7, {0: (0.0, [Segment(1, 2, "x")]), 1: (600.0, [])})
    assert load_parts(7) == {0: (0.0, [Segment(1, 2, "x")]), 1: (600.0, [])}
    assert load_parts(8) == {}


def test_split_uses_word_times_and_sentence_ends():
    text = "Aaj hum loops padhenge. Pehle for loop dekhte hain aur phir while loop ko samjhenge"
    tokens = text.split()
    words = [Segment(i * 1.0, i * 1.0 + 0.8, t.rstrip(".")) for i, t in enumerate(tokens)]
    lines = split_segments([Segment(0, len(tokens), text)], words)
    assert lines[0] == Segment(0, 3.8, "Aaj hum loops padhenge.")  # punctuation kept from the segment text
    assert all(line.end - line.start <= 7 for line in lines)
    assert " ".join(line.text for line in lines) == text
    assert [line.start for line in lines] == sorted(line.start for line in lines)


def test_split_without_word_times_estimates_and_keeps_short_lines():
    short = Segment(0, 3, "chhoti line")
    long = Segment(170, 210, " ".join(f"word{i}" for i in range(80)))
    lines = split_segments([short, long])
    assert lines[0] == short
    assert len(lines) > 6 and lines[1].start == 170 and lines[-1].end == pytest.approx(210)
    assert all(line.end - line.start <= 7.01 and len(line.text) <= 84 for line in lines[1:])


def test_old_long_transcripts_are_split_on_load(tmp_path, monkeypatch):
    import json

    import app.transcribe as transcribe

    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path)
    (tmp_path / "5.json").write_text(json.dumps([[170, 210, " ".join(["shabd"] * 60)]]), encoding="utf-8")
    lines = load_transcript(5)
    assert len(lines) > 4
    assert len(json.loads((tmp_path / "5.json").read_text(encoding="utf-8"))) == len(lines)
    assert (tmp_path / "5.srt").read_text(encoding="utf-8").count("-->") == len(lines)
    save_transcript(6, [Segment(0, 2, "short")])
    assert load_transcript(6) == [Segment(0, 2, "short")]


def test_audio_chunks_overlap_and_offsets(tmp_path):
    source = tmp_path / "tone.wav"
    make_tone(source, 25)
    chunks = list(iter_chunks(str(source), chunk_s=10, overlap_s=1))
    assert [c.index for c in chunks] == [0, 1, 2]
    assert [c.offset for c in chunks] == [0, 10, 20]
    # chunk edges follow audio frame boundaries, so allow a frame or two of slack
    assert [c.duration for c in chunks] == pytest.approx([11.0, 11.0, 5.0], abs=0.15)
    assert abs(mp3_seconds(chunks[0].data) - chunks[0].duration) < 0.2  # a valid MP3


def test_audio_chunks_skip_already_done(tmp_path):
    source = tmp_path / "tone.wav"
    make_tone(source, 25)
    assert [c.index for c in iter_chunks(str(source), skip={0, 1}, chunk_s=10, overlap_s=1)] == [2]


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload


def test_groq_parses_segments(monkeypatch):
    sent = {}

    def fake_post(url, headers, files, data, timeout):
        sent.update(url=url, auth=headers["Authorization"], fields=dict(data))
        return FakeResponse(200, {"segments": [{"start": 0.0, "end": 2.5, "text": " Namaste "}]})

    monkeypatch.setattr(groq_client.requests, "post", fake_post)
    assert groq_client.transcribe("key", b"mp3") == [Segment(0.0, 2.5, "Namaste")]
    assert sent["url"].endswith("/audio/transcriptions") and sent["auth"] == "Bearer key"
    assert sent["fields"]["model"] == "whisper-large-v3" and sent["fields"]["language"] == "en"


@pytest.mark.parametrize("status,error", [(401, groq_client.InvalidKey), (500, groq_client.GroqError)])
def test_groq_errors(monkeypatch, status, error):
    monkeypatch.setattr(groq_client.requests, "post", lambda *a, **k: FakeResponse(status, {"error": {"message": "x"}}))
    with pytest.raises(error):
        groq_client.transcribe("key", b"mp3")


def test_groq_rate_limit_reports_retry_after(monkeypatch):
    monkeypatch.setattr(groq_client.requests, "post",
                        lambda *a, **k: FakeResponse(429, {"error": {"message": "slow down"}}, {"retry-after": "12"}))
    with pytest.raises(groq_client.RateLimited) as info:
        groq_client.transcribe("key", b"mp3")
    assert info.value.retry_after == 12
