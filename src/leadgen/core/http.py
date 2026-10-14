"""Shared HTTP client settings."""

from __future__ import annotations

import httpx

from leadgen import __version__

USER_AGENT = f"LeadGenerator/{__version__}"


def client() -> httpx.Client:
    """An `httpx.Client` with sane timeouts, a real User-Agent and redirects enabled."""
    return httpx.Client(
        timeout=httpx.Timeout(20.0, connect=10.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def get_text(url: str, max_bytes: int = 2_000_000) -> str:
    """GET `url` and return the body as text, reading at most `max_bytes` bytes.

    Raises httpx.HTTPStatusError on 4xx/5xx and httpx.RequestError on network problems.
    """
    chunks: list[bytes] = []
    size = 0
    with client() as http, http.stream("GET", url) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            piece = chunk[: max_bytes - size]
            chunks.append(piece)
            size += len(piece)
            if size >= max_bytes:
                break
        encoding = response.encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="replace")
