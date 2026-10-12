import httpx
import pytest

from panel.youtube import Video, parse_video, thumbnail

VID = "Ab-Cd_Ef012"
WATCH = f"https://www.youtube.com/watch?v={VID}"


@pytest.mark.parametrize("raw", [
    f"https://youtu.be/{VID}?si=Zx9Yw8Vu7Ts6",
    f"https://youtube.com/shorts/{VID}?is=Qr5St4Uv3Wx2",
    f"https://youtube.com/shorts/{VID}?feature=shared",
    f"https://www.youtube.com/watch?v={VID}&list=PLabc&index=2&t=42s",
    f"https://www.youtube.com/watch?feature=youtu.be&v={VID}",
    f"https://www.youtube.com/embed/{VID}?autoplay=1",
    f"https://m.youtube.com/watch?v={VID}&pp=ygUE",
    f"https://www.youtube-nocookie.com/embed/{VID}",
    f"youtu.be/{VID}",
    f"www.youtube.com/shorts/{VID}",
    f"http://youtu.be/{VID}",
    f"https://youtu.be/{VID}.",
    f"(https://youtu.be/{VID}),",
    f"Mój filmik: https://youtu.be/{VID}?si=abc - dzięki!",
    f"Link:\nhttps://youtu.be/{VID}\nPozdrawiam",
])
def test_youtube_shapes(raw):
    assert parse_video(raw) == Video("youtube", WATCH, VID)


@pytest.mark.parametrize("raw, url", [
    ("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz?usp=sharing",
     "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz?usp=sharing"),
    ("Filmik: https://drive.google.com/file/d/1ZyXwVuTsRqPoNmLk/view?usp=drive_link.",
     "https://drive.google.com/file/d/1ZyXwVuTsRqPoNmLk/view?usp=drive_link"),
    ("drive.google.com/file/d/1ZyXwVuTsRqPoNmLk/view",
     "https://drive.google.com/file/d/1ZyXwVuTsRqPoNmLk/view"),
])
def test_drive_links(raw, url):
    assert parse_video(raw) == Video("drive", url, None)


@pytest.mark.parametrize("raw", [
    "",
    None,
    "Nagram filmik w przyszłym tygodniu, wyślę mailem",
    "https://vimeo.com/123456789",
    "https://www.youtube.com/@teatrsgh",
    "https://youtu.be/tooShort",
    f"https://notyoutube.com/watch?v={VID}",
    "https://drive.google.com/",
])
def test_no_valid_link(raw):
    assert parse_video(raw) is None


def mock_transport(statuses):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        status = statuses[request.url.path.rsplit("/", 1)[1]]
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, content=b"jpeg-bytes", headers={"content-type": "image/jpeg"})

    return httpx.MockTransport(handler), calls


def test_thumbnail_prefers_maxres():
    transport, calls = mock_transport({"maxresdefault.jpg": 200})
    assert thumbnail(VID, transport=transport) == (b"jpeg-bytes", "image/jpeg")
    assert calls == [f"/vi/{VID}/maxresdefault.jpg"]


def test_thumbnail_falls_back_to_hq():
    transport, calls = mock_transport({"maxresdefault.jpg": 404, "hqdefault.jpg": 200})
    assert thumbnail(VID, transport=transport) == (b"jpeg-bytes", "image/jpeg")
    assert calls == [f"/vi/{VID}/maxresdefault.jpg", f"/vi/{VID}/hqdefault.jpg"]


@pytest.mark.parametrize("statuses", [
    {"maxresdefault.jpg": 404, "hqdefault.jpg": 404},
    {"maxresdefault.jpg": 500},
    {"maxresdefault.jpg": httpx.ConnectTimeout("timeout")},
])
def test_thumbnail_failures_return_none(statuses):
    transport, _ = mock_transport(statuses)
    assert thumbnail(VID, transport=transport) is None
