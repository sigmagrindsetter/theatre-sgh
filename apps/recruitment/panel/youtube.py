"""Video link from the form answer: YouTube or Google Drive, normalised; YouTube thumbnail fetch."""
import re
from typing import NamedTuple, Optional
from urllib.parse import parse_qs, urlsplit

import httpx

LINK = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:[a-z0-9-]+\.)*(?:youtube(?:-nocookie)?\.com|youtu\.be|drive\.google\.com)"
    r"(?:[/?][^\s<>\"']*)?",
    re.IGNORECASE,
)
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}(?![A-Za-z0-9_-])")
TRAILING = ".,;:!?)]}>"


class Video(NamedTuple):
    kind: str  # "youtube" | "drive"
    url: str  # https://www.youtube.com/watch?v=<id> or the Drive URL
    video_id: Optional[str]


def parse_video(raw):
    """First YouTube or Drive link in the answer, or None (the submission is then skipped)."""
    for match in LINK.finditer(raw or ""):
        link = match.group().rstrip(TRAILING)
        if not re.match(r"https?://", link, re.IGNORECASE):
            link = "https://" + link
        parts = urlsplit(link)
        host = parts.hostname or ""
        if host == "drive.google.com":
            if parts.path.strip("/"):
                return Video("drive", "https://" + link.split("://", 1)[1], None)
            continue
        video_id = _youtube_id(host, parts)
        if video_id:
            return Video("youtube", f"https://www.youtube.com/watch?v={video_id}", video_id)
    return None


def _youtube_id(host, parts):
    segments = parts.path.split("/")
    if host == "youtu.be":
        candidate = segments[1] if len(segments) > 1 else ""
    elif parts.path in ("/watch", "/watch/"):
        candidate = parse_qs(parts.query).get("v", [""])[0]
    elif len(segments) > 2 and segments[1] in ("shorts", "embed"):
        candidate = segments[2]
    else:
        return None
    match = VIDEO_ID.match(candidate)
    return match.group() if match else None


def thumbnail(video_id, transport=None):
    """(bytes, content_type) of the largest YouTube thumbnail, or None; network errors never escape."""
    try:
        with httpx.Client(timeout=10, transport=transport) as http:
            for name in ("maxresdefault.jpg", "hqdefault.jpg"):
                response = http.get(f"https://i.ytimg.com/vi/{video_id}/{name}")
                if response.status_code == 200:
                    return response.content, response.headers.get("content-type", "image/jpeg")
                if response.status_code != 404:
                    return None
    except httpx.HTTPError:
        return None
    return None
