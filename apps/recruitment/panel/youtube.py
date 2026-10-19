"""Video link from the form answer: YouTube or Google Drive, normalised; Drive folder → video; thumbnail fetch."""
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
DRIVE_FILE = re.compile(r"/file/d/([A-Za-z0-9_-]{10,})")
DRIVE_FOLDER = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
FOLDER_ENTRY = re.compile(r'id="entry-([A-Za-z0-9_-]{10,})"')
# Drive serves thumbnails as application/binary
IMAGE_TYPES = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png"))


class Video(NamedTuple):
    kind: str  # "youtube" | "drive"
    url: str  # https://www.youtube.com/watch?v=<id>, https://drive.google.com/file/d/<id>/view or the Drive URL
    video_id: Optional[str]  # YouTube id or Drive file id; None for a Drive folder


def parse_video(raw):
    """First YouTube or Drive link in the answer, or None (the submission is then skipped)."""
    for match in LINK.finditer(raw or ""):
        link = match.group().rstrip(TRAILING)
        if not re.match(r"https?://", link, re.IGNORECASE):
            link = "https://" + link
        parts = urlsplit(link)
        host = parts.hostname or ""
        if host == "drive.google.com":
            file = DRIVE_FILE.search(parts.path)
            if file:
                return drive_file(file.group(1))
            if parts.path.strip("/"):
                return Video("drive", "https://" + link.split("://", 1)[1], None)
            continue
        video_id = _youtube_id(host, parts)
        if video_id:
            return Video("youtube", f"https://www.youtube.com/watch?v={video_id}", video_id)
    return None


def drive_file(file_id):
    return Video("drive", f"https://drive.google.com/file/d/{file_id}/view", file_id)


def drive_video(video, transport=None):
    """A Drive folder link resolved to the first video in it, else the video unchanged; network errors never escape.
    Reads Drive's public folder view, so it works for the "anyone with the link" folders the form asks for."""
    folder = DRIVE_FOLDER.search(urlsplit(video.url).path) if video.kind == "drive" and not video.video_id else None
    if not folder:
        return video
    try:
        with httpx.Client(timeout=10, transport=transport, follow_redirects=True) as http:
            response = http.get("https://drive.google.com/embeddedfolderview", params={"id": folder.group(1)})
    except httpx.HTTPError:
        return video
    if response.status_code != 200:
        return video
    for entry in response.text.split('class="flip-entry"')[1:]:
        file = FOLDER_ENTRY.match(entry.lstrip())
        if file and "/type/video/" in entry:
            return drive_file(file.group(1))
    return video


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


def drive_thumbnail(file_id, transport=None):
    """(bytes, content_type) of the Drive thumbnail of a publicly shared file, or None; network errors never escape."""
    try:
        with httpx.Client(timeout=10, transport=transport, follow_redirects=True) as http:
            response = http.get("https://drive.google.com/thumbnail", params={"id": file_id, "sz": "w1280"})
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    for magic, content_type in IMAGE_TYPES:
        if response.content.startswith(magic):
            return response.content, content_type
    return None
