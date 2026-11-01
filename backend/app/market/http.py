"""Outbound HTTP for market data (PLAN 5.3, 5.5, 6.2). Standard library only.

- One GET at a time through a `Transport`: a callable that takes the URL, the
  headers and a timeout, and returns an `HttpResponse`. The default uses
  urllib.request, so the runtime image needs no extra package. Tests pass a
  fake transport, so no test ever reaches the network.
- A URL carries only a ticker or a public index id and dates. A key goes in a
  header, never in the URL.
- Redirects are never followed. urllib would copy every header, the Tiingo
  key included, to whatever host a 3xx names. A 3xx comes back as a
  response, and callers treat it as `bad_response`.
- Nothing here logs. Errors carry a short code (`timeout`, `network`), never a
  URL, a header or a body, so a caller can log them as they are.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.demo.context import DemoRefused, in_demo

# Yahoo answers 429 without a browser-like User-Agent (checked Oct 21, 2026).
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30.0
MAX_BYTES = 8 * 1024 * 1024  # a price response is a few KB


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class NetworkError(Exception):
    """The request did not get an HTTP answer. `code` is timeout or network."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


Transport = Callable[[str, Mapping[str, str], float], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so headers never go to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None  # urllib then raises HTTPError with the 3xx status


# Module level so tests can swap it (tests/conftest.py blocks the network).
_OPENER = urllib.request.build_opener(_NoRedirect())


def open_url(req: urllib.request.Request, timeout: float):  # noqa: ANN201
    """urlopen without redirects. Every outbound GET in the app goes through here.
    Raises DemoRefused in demo (docs/plans/demo-mode.md)."""
    if in_demo():
        raise DemoRefused("outbound call refused in demo")
    return _OPENER.open(req, timeout=timeout)


def url_error_code(exc: urllib.error.URLError) -> str:
    """A connect timeout arrives wrapped in URLError."""
    return "timeout" if isinstance(exc.reason, TimeoutError) else "network"


def urllib_transport(
    url: str, headers: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT
) -> HttpResponse:
    """GET with urllib. HTTP error statuses come back as a response, not an exception."""
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with open_url(req, timeout) as r:
            return HttpResponse(r.status, r.read(MAX_BYTES))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(MAX_BYTES)
        except OSError:
            body = b""
        return HttpResponse(exc.code, body)
    except TimeoutError:
        raise NetworkError("timeout") from None
    except urllib.error.URLError as exc:
        raise NetworkError(url_error_code(exc)) from None
    except OSError:
        raise NetworkError("network") from None
