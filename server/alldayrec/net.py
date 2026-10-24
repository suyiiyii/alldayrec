"""The server's outbound HTTP (the ASR providers, the LLM, the upload bridge, the metrics push), async on httpx2.

One client, so one connection, a request, as urllib made them. Failures raise what urllib raised, so the calls table
(asr.failure), the Grafana result labels and the callers' handling (ask's Retry-After) read as before: an HTTP error
status urllib's HTTPError (code, reason, headers, body), a timeout TimeoutError, any other transport failure URLError.
"""
import contextlib
import io
import logging
import urllib.error

import httpx2

TRANSPORT: httpx2.AsyncBaseTransport | None = None  # tests: an httpx2.MockTransport
logging.getLogger("httpx2").setLevel(logging.WARNING)  # a line a request otherwise: every URL in server.log, presigned ones too


def client(timeout: float) -> httpx2.AsyncClient:
    """timeout: for connecting and for each read and write, as urllib's (a long answer may take longer in all)."""
    return httpx2.AsyncClient(transport=TRANSPORT, timeout=timeout, follow_redirects=True)  # urllib follows them too


@contextlib.contextmanager
def errors():
    """httpx2's transport failures as urllib's."""
    try:
        yield
    except httpx2.TimeoutException as e:
        raise TimeoutError(str(e) or "timed out") from e
    except httpx2.TransportError as e:
        raise urllib.error.URLError(str(e) or type(e).__name__) from e


def http_error(r: httpx2.Response, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(str(r.url), r.status_code, r.reason_phrase, r.headers, io.BytesIO(body))


async def request(method: str, url: str, timeout: float = 60, **kw) -> httpx2.Response:
    """One request, read whole; raises on an HTTP error status (as urllib.request.urlopen)."""
    with errors():
        async with client(timeout) as c:
            r = await c.request(method, url, **kw)
    if r.status_code >= 400:
        raise http_error(r, r.content)
    return r
