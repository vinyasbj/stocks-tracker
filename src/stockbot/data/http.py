import ssl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
import truststore

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# The OS trust store fetches intermediates that some servers (SEBI) leave out of the chain.
_SSL = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def get(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: float = 20) -> httpx.Response:
    resp = httpx.get(
        url,
        params=params,
        headers={**BROWSER_HEADERS, **(headers or {})},
        timeout=timeout,
        follow_redirects=True,
        verify=_SSL,
    )
    resp.raise_for_status()
    return resp


def parse_date(value: str | None, *formats: str) -> datetime | None:
    """Parse RFC-822 (RSS) dates, or any of `formats`; naive results are taken as UTC."""
    if not value:
        return None
    value = value.strip()
    candidates = []
    try:
        candidates.append(parsedate_to_datetime(value))
    except (TypeError, ValueError):
        pass
    for fmt in formats:
        try:
            candidates.append(datetime.strptime(value, fmt))
        except ValueError:
            pass
    if not candidates:
        return None
    dt = candidates[0]
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
