"""Safe, cancellable, size-capped HTTPS downloader for untrusted URLs."""
from __future__ import annotations
import os
import re
import threading
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from . import config


class DownloadError(Exception):
    pass


@dataclass
class DownloadResult:
    ok: bool
    path: str = ""
    bytes_written: int = 0
    error: str = ""
    was_https: bool = True


def _safe_filename(url: str, content_disposition: str | None) -> str:
    name = None
    if content_disposition:
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition)
        if m:
            name = m.group(1)
    if not name:
        name = os.path.basename(urlparse(url).path) or "download"
    # Strip any path separators first so a Content-Disposition header can't
    # smuggle a relative path in before the charset filter below runs.
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    name = re.sub(r"\.\.+", ".", name)       # collapse ".." sequences
    name = name.lstrip(".")[:200]             # no hidden-dotfile surprises
    return name or "download"


def download(url: str, allow_http: bool = False,
             cancel_event: threading.Event | None = None,
             progress_cb=None) -> DownloadResult:
    """Stream `url` to config.DOWNLOADS_DIR with a hard size cap. Refuses
    plain HTTP unless allow_http=True (the UI must warn and get explicit
    confirmation before ever passing that)."""
    parsed = urlparse(url)
    was_https = parsed.scheme == "https"
    if parsed.scheme not in ("https", "http"):
        return DownloadResult(ok=False, error=f"unsupported URL scheme: {parsed.scheme}")
    if parsed.scheme == "http" and not allow_http:
        return DownloadResult(ok=False, error="refusing plain HTTP without explicit confirmation",
                               was_https=False)

    try:
        with requests.get(url, stream=True, timeout=config.DOWNLOAD_TIMEOUT_S,
                           headers={"User-Agent": "ThemeSwitch/1.0"}) as resp:
            resp.raise_for_status()

            declared_len = resp.headers.get("Content-Length")
            if declared_len and int(declared_len) > config.MAX_DOWNLOAD_BYTES:
                return DownloadResult(ok=False, error="remote file exceeds the download size limit")

            fname = _safe_filename(url, resp.headers.get("Content-Disposition"))
            dest = os.path.join(config.DOWNLOADS_DIR, fname)
            base, ext = os.path.splitext(dest)
            i = 1
            while os.path.exists(dest):
                dest = f"{base}_{i}{ext}"
                i += 1

            written = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=config.DOWNLOAD_CHUNK):
                    if cancel_event is not None and cancel_event.is_set():
                        f.close()
                        os.remove(dest)
                        return DownloadResult(ok=False, error="cancelled")
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > config.MAX_DOWNLOAD_BYTES:
                        f.close()
                        os.remove(dest)
                        return DownloadResult(ok=False, error="download exceeded the size limit, aborted")
                    f.write(chunk)
                    if progress_cb:
                        progress_cb(written, int(declared_len) if declared_len else None)

            return DownloadResult(ok=True, path=dest, bytes_written=written, was_https=was_https)

    except requests.exceptions.Timeout:
        return DownloadResult(ok=False, error="download timed out")
    except requests.exceptions.RequestException as e:
        return DownloadResult(ok=False, error=f"download failed: {e}")
    except OSError as e:
        return DownloadResult(ok=False, error=f"could not write file: {e}")


def fetch_text(url: str, max_bytes: int = 2_000_000) -> str | None:
    """Small GET for HTML/JSON pages during analysis — same size cap spirit,
    much smaller ceiling since this is just page/API content, not an asset."""
    try:
        with requests.get(url, stream=True, timeout=config.DOWNLOAD_TIMEOUT_S,
                           headers={"User-Agent": "ThemeSwitch/1.0",
                                    "Accept": "text/html,application/json;q=0.9,*/*;q=0.1"}) as resp:
            resp.raise_for_status()
            buf = bytearray()
            for chunk in resp.iter_content(chunk_size=config.DOWNLOAD_CHUNK):
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    break
            return buf.decode(resp.encoding or "utf-8", errors="replace")
    except requests.exceptions.RequestException:
        return None
