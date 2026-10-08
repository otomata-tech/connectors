"""A multipart/form-data body streamed from a binary file object.

`requests` builds a `files=` body entirely in memory, which a file of a
hundred megabytes rules out. `MultipartStream` yields the body piece by piece
instead: the form fields and the part header, then the file read in chunks
of at most `chunk_size` bytes, then the closing boundary. Its length is known
in advance (`len()`, from the file's size by seek/tell), so `requests` sends
it with a `Content-Length`, not chunked transfer encoding.
"""

from __future__ import annotations

import uuid
from typing import BinaryIO, Iterator, Mapping

CHUNK_SIZE = 256 * 1024


def _quote(value: str) -> str:
    """A value for a quoted header parameter: no quote, CR or LF can break
    out of it (percent-encoded, as browsers do)."""
    return (value.replace("\r", "%0D").replace("\n", "%0A")
            .replace('"', "%22"))


def _remaining_size(fileobj: BinaryIO) -> int:
    """Bytes from the current position to the end, position left unchanged."""
    try:
        start = fileobj.tell()
        end = fileobj.seek(0, 2)
        fileobj.seek(start)
    except (AttributeError, OSError, ValueError) as e:
        raise ValueError("the file object must be seekable, to know its size "
                         "before sending it") from e
    return end - start


class MultipartStream:
    """One multipart/form-data body: text `fields`, then one file part.

    Iterable once. `content_type` carries the boundary; `len()` is the exact
    byte length of the body.
    """

    def __init__(self, fields: Mapping[str, str], file_field: str,
                 fileobj: BinaryIO, filename: str, content_type: str, *,
                 chunk_size: int = CHUNK_SIZE):
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        boundary = uuid.uuid4().hex
        self.content_type = f"multipart/form-data; boundary={boundary}"
        head = []
        for key, value in fields.items():
            head.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{_quote(key)}"\r\n\r\n'
                f"{value}\r\n")
        head.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{_quote(file_field)}"; '
            f'filename="{_quote(filename)}"\r\n'
            f"Content-Type: {_quote(content_type)}\r\n\r\n")
        self._head = "".join(head).encode("utf-8")
        self._tail = f"\r\n--{boundary}--\r\n".encode("ascii")
        self._fileobj = fileobj
        self._chunk_size = chunk_size
        self.file_size = _remaining_size(fileobj)
        self._consumed = False

    def __len__(self) -> int:
        return len(self._head) + self.file_size + len(self._tail)

    def __iter__(self) -> Iterator[bytes]:
        if self._consumed:
            raise RuntimeError("a multipart stream can be sent only once")
        self._consumed = True
        yield self._head
        remaining = self.file_size
        while remaining > 0:
            size = min(self._chunk_size, remaining)
            chunk = self._fileobj.read(size)
            if not chunk:
                raise ValueError(
                    f"the file ended {remaining} bytes before the size "
                    "measured when the upload started")
            if len(chunk) > size:
                raise ValueError(f"the file object returned {len(chunk)} "
                                 f"bytes for a read of {size}")
            remaining -= len(chunk)
            yield chunk
        yield self._tail
