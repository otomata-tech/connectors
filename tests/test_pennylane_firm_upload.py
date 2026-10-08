"""Streamed multipart upload of PennylaneFirmClient.upload_dms_file.

Files run to a hundred megabytes: the body must never be built in memory.
A file object that refuses any read without a size, or beyond a bound, proves
that the file is only ever read in bounded chunks; the body is then parsed
back to prove it is a valid multipart form. The last test sends it through the
real `requests` transport to a loopback server.
"""

import email.parser
import email.policy
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from oto.tools.pennylane_firm import PennylaneFirmClient, TokenRateLimiter
from oto.tools.pennylane_firm.multipart import CHUNK_SIZE, MultipartStream

SIZE = 5 * 1024 * 1024 + 123          # several MB, not a multiple of the chunk


class _BoundedFile(io.BytesIO):
    """A file that fails any read without a size or above `limit` bytes."""

    def __init__(self, data, limit):
        super().__init__(data)
        self.limit = limit
        self.reads = []

    def read(self, size=-1):
        if size is None or size < 0 or size > self.limit:
            raise AssertionError(f"unbounded read ({size!r})")
        self.reads.append(size)
        return super().read(size)


def _payload():
    return bytes(range(256)) * (SIZE // 256) + b"\0" * (SIZE % 256)


def _parts(content_type, body):
    """{field name: (filename, content type, payload)} of a multipart body."""
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    assert message.is_multipart()
    out = {}
    for part in message.iter_parts():
        out[part.get_param("name", header="content-disposition")] = (
            part.get_filename(), part.get_content_type(),
            part.get_payload(decode=True))
    return out


def test_the_file_is_read_in_bounded_chunks_and_the_body_is_valid():
    data = _payload()
    f = _BoundedFile(data, CHUNK_SIZE)
    stream = MultipartStream({"parent_folder_id": "567", "name": "Report.pdf"},
                             "file", f, "report.pdf", "application/pdf")
    assert f.reads == []                       # building reads nothing
    body = b"".join(stream)
    assert len(body) == len(stream)
    assert max(f.reads) <= CHUNK_SIZE and len(f.reads) == -(-SIZE // CHUNK_SIZE)
    parts = _parts(stream.content_type, body)
    assert parts["file"] == ("report.pdf", "application/pdf", data)
    assert parts["name"][2] == b"Report.pdf"
    assert parts["parent_folder_id"][2] == b"567"


def test_the_file_is_read_from_its_current_position():
    f = io.BytesIO(b"HEADERpayload")
    f.seek(6)
    stream = MultipartStream({}, "file", f, "a.bin", "application/octet-stream")
    assert stream.file_size == 7
    assert _parts(stream.content_type, b"".join(stream))["file"][2] == b"payload"


def test_quotes_and_line_breaks_cannot_break_out_of_the_header():
    stream = MultipartStream({}, "file", io.BytesIO(b"x"),
                             'a"b\r\nX-Injected: 1.pdf', "application/pdf")
    body = b"".join(stream)
    assert b"\r\nX-Injected" not in body
    assert _parts(stream.content_type, body)["file"][0] == "a%22b%0D%0AX-Injected: 1.pdf"


def test_a_file_that_shrinks_during_the_upload_raises():
    f = io.BytesIO(b"0123456789")
    stream = MultipartStream({}, "file", f, "a.bin", "application/octet-stream")
    f.truncate(4)
    with pytest.raises(ValueError, match="ended"):
        b"".join(stream)


def test_a_stream_is_sent_once():
    stream = MultipartStream({}, "file", io.BytesIO(b"x"), "a", "text/plain")
    b"".join(stream)
    with pytest.raises(RuntimeError):
        b"".join(stream)


def test_a_non_seekable_file_is_refused():
    class _Pipe:
        def read(self, size=-1):
            return b""

    with pytest.raises(ValueError, match="seekable"):
        MultipartStream({}, "file", _Pipe(), "a", "text/plain")


def test_content_type_is_guessed_or_falls_back():
    received = []

    class _Session:
        headers = {}

        def request(self, method, url, **kw):
            received.append(kw["headers"]["Content-Type"])
            body = b"".join(kw["data"])
            received.append(_parts(kw["headers"]["Content-Type"], body)["file"][1])

            class _R:
                status_code, content, text, headers = 201, b"{}", "{}", {}

                def json(self):
                    return {}
            return _R()

    c = PennylaneFirmClient(token="tok", session=_Session(),
                            rate_limiter=TokenRateLimiter(10**6, 1.0))
    c.upload_dms_file(7, io.BytesIO(b"x"), "a.unknownext", 1)
    c.upload_dms_file(7, io.BytesIO(b"x"), "a.pdf", 1)
    c.upload_dms_file(7, io.BytesIO(b"x"), "a.pdf", 1, content_type="image/png")
    assert received[1::2] == ["application/octet-stream", "application/pdf", "image/png"]


def test_requests_streams_the_body_with_a_content_length():
    """The real transport, to a loopback server: Content-Length announced, no
    chunked encoding, the file read in bounded chunks, a valid form received."""
    seen = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["headers"] = dict(self.headers)
            length = int(self.headers["Content-Length"])
            seen["body"] = self.rfile.read(length)
            answer = json.dumps({"id": 11, "name": "Report.pdf"}).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        session = requests.Session()
        session.trust_env = False             # no proxy for the loopback
        c = PennylaneFirmClient(token="tok", session=session,
                                rate_limiter=TokenRateLimiter(10**6, 1.0))
        c.BASE_URL = f"http://127.0.0.1:{server.server_address[1]}"
        data = _payload()
        f = _BoundedFile(data, CHUNK_SIZE)
        out = c.upload_dms_file(7, f, "report.pdf", 567, name="Report.pdf",
                                timeout=(5, 30))
    finally:
        server.shutdown()
        server.server_close()
    assert out == {"id": 11, "name": "Report.pdf"}
    assert seen["path"] == "/companies/7/dms/files"
    assert "Transfer-Encoding" not in seen["headers"]
    assert int(seen["headers"]["Content-Length"]) == len(seen["body"])
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert max(f.reads) <= CHUNK_SIZE
    parts = _parts(seen["headers"]["Content-Type"], seen["body"])
    assert parts["file"] == ("report.pdf", "application/pdf", data)
    assert parts["name"][2] == b"Report.pdf"
    assert parts["parent_folder_id"][2] == b"567"
