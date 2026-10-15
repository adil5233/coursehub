def match_subtitle(video_stem: str, subtitles: dict[str, str]) -> str | None:
    """Find a subtitle whose stem equals the video stem, or is like "<stem>.en"."""
    key = video_stem.lower()
    if key in subtitles:
        return subtitles[key]
    for stem, ref in subtitles.items():
        if stem.startswith(key + "."):
            return ref
    return None
