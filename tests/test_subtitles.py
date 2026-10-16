from app.player.subtitles import CueTrack, parse_subtitles


def test_parse_srt_and_vtt(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_bytes(
        "\ufeff1\r\n00:00:01,000 --> 00:00:03,500\r\n<i>Hello</i> guys,\r\nwelcome\r\n\r\n"
        "2\r\n00:01:02,250 --> 00:01:04,000\r\n{\\an8}Loops\r\n".encode("utf-8")
    )
    cues = parse_subtitles(str(srt))
    assert [(c.start, c.end, c.text) for c in cues] == [(1000, 3500, "Hello guys,\nwelcome"), (62250, 64000, "Loops")]

    vtt = tmp_path / "b.vtt"
    vtt.write_text("WEBVTT\n\nintro\n00:05.000 --> 00:07.000 align:center\nNamaste\n", encoding="utf-8")
    assert [(c.start, c.end, c.text) for c in parse_subtitles(str(vtt))] == [(5000, 7000, "Namaste")]


def test_cue_lookup(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nA\n\n2\n00:00:05,000 --> 00:00:06,000\nB\n", encoding="utf-8")
    track = CueTrack(parse_subtitles(str(srt)))
    assert [track.text_at(ms) for ms in (0, 1000, 1999, 2000, 4000, 5500, 9000)] == ["", "A", "A", "", "", "B", ""]
