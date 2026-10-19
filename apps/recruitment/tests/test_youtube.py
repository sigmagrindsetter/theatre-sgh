import httpx
import pytest

from panel.youtube import Video, drive_thumbnail, drive_video, parse_video, thumbnail

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


FOLDER = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz?usp=sharing"
FILE_ID = "1ZyXwVuTsRqPoNmLk"
DRIVE_FILE = Video("drive", f"https://drive.google.com/file/d/{FILE_ID}/view", FILE_ID)


def test_drive_folder_link_kept_as_is():
    assert parse_video(FOLDER) == Video("drive", FOLDER, None)


@pytest.mark.parametrize("raw", [
    f"Filmik: https://drive.google.com/file/d/{FILE_ID}/view?usp=drive_link.",
    f"drive.google.com/file/d/{FILE_ID}/view",
    f"https://drive.google.com/file/d/{FILE_ID}/preview",
    f"https://drive.google.com/file/d/{FILE_ID}",
])
def test_drive_file_links(raw):
    assert parse_video(raw) == DRIVE_FILE


def folder_view(*entries):
    """Drive's public folder view with (file id, MIME type) entries, shaped like the real page."""
    return "".join(
        f'<div class="flip-entry" id="entry-{file_id}" tabindex="0" role="link"><div class="flip-entry-info">'
        f'<a href="https://drive.google.com/file/d/{file_id}/view?usp=drive_web" target="_blank">'
        f'<img src="https://drive-thirdparty.googleusercontent.com/16/type/{mime}" alt=""/></a></div></div>'
        for file_id, mime in entries)


def folder_transport(response):
    def handler(request):
        assert request.url.path == "/embeddedfolderview"
        assert request.url.params["id"] == "1AbCdEfGhIjKlMnOpQrStUvWxYz"
        if isinstance(response, Exception):
            raise response
        return response
    return httpx.MockTransport(handler)


def test_drive_folder_resolves_to_its_first_video():
    html = folder_view(("1NotesPdfAbc", "application/pdf"), (FILE_ID, "video/quicktime"), ("1SecondVidXyz", "video/mp4"))
    transport = folder_transport(httpx.Response(200, text=html))
    assert drive_video(parse_video(FOLDER), transport=transport) == DRIVE_FILE


@pytest.mark.parametrize("response", [
    httpx.Response(200, text=folder_view(("1NotesPdfAbc", "application/pdf"))),
    httpx.Response(200, text=""),
    httpx.Response(404),
    httpx.ConnectTimeout("timeout"),
])
def test_drive_folder_without_a_reachable_video_stays_a_folder(response):
    video = parse_video(FOLDER)
    assert drive_video(video, transport=folder_transport(response)) == video


@pytest.mark.parametrize("video", [DRIVE_FILE, Video("youtube", WATCH, VID)])
def test_drive_video_leaves_files_and_youtube_alone(video):
    transport = httpx.MockTransport(lambda request: pytest.fail("no request expected"))
    assert drive_video(video, transport=transport) == video


def thumbnail_transport(response):
    def handler(request):
        if request.url.host == "drive.google.com":
            assert request.url.path == "/thumbnail" and request.url.params["id"] == FILE_ID
            return httpx.Response(302, headers={"location": f"https://lh3.googleusercontent.com/d/{FILE_ID}=w1280"})
        if isinstance(response, Exception):
            raise response
        return response
    return httpx.MockTransport(handler)


@pytest.mark.parametrize("content, content_type", [(b"\xff\xd8\xff\xe0jpeg", "image/jpeg"),
                                                   (b"\x89PNG\r\n", "image/png")])
def test_drive_thumbnail_follows_the_redirect_and_sniffs_the_type(content, content_type):
    response = httpx.Response(200, content=content, headers={"content-type": "application/binary"})
    assert drive_thumbnail(FILE_ID, transport=thumbnail_transport(response)) == (content, content_type)


@pytest.mark.parametrize("response", [httpx.Response(200, content=b"<html>sign in</html>"), httpx.Response(403),
                                      httpx.ConnectTimeout("timeout")])
def test_drive_thumbnail_failures_return_none(response):
    assert drive_thumbnail(FILE_ID, transport=thumbnail_transport(response)) is None


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
