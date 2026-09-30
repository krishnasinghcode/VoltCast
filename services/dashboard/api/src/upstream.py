"""Thin httpx proxy helper for data-gen / ML / ingestion calls.

Upstream status codes are passed through; upstream error bodies are normalised to the contract error shape
(FastAPI peers wrap errors as {"detail": {"error": {...}}}). Connection failures become 502
UPSTREAM_UNAVAILABLE so the dashboard can show "service down" instead of a generic 500.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from fastapi.responses import JSONResponse

from .errors import error_body

log = logging.getLogger(__name__)


def _normalise_error(status: int, payload: Any, service: str) -> dict:
    if isinstance(payload, dict):
        if "error" in payload and isinstance(payload["error"], dict):
            return payload
        detail = payload.get("detail")
        if isinstance(detail, dict) and "error" in detail:
            return detail
        if detail is not None:
            code = "VALIDATION_ERROR" if status == 422 else "UPSTREAM_ERROR"
            return error_body(code, f"{service} rejected the request", {"upstream": detail})
    return error_body("UPSTREAM_ERROR", f"{service} returned HTTP {status}", {"upstream": payload})


async def proxy(method: str, base_url: str, path: str, service: str, json_body: Optional[Any] = None,
                params: Optional[dict] = None, timeout: float = 15.0) -> JSONResponse:
    url = f"{base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, json=json_body, params=params)
    except httpx.TimeoutException:
        log.warning("%s timed out: %s %s", service, method, url)
        return JSONResponse(status_code=504, content=error_body(
            "UPSTREAM_TIMEOUT", f"{service} did not respond within {timeout:.0f}s", {"service": service}))
    except httpx.HTTPError as exc:
        log.warning("%s unreachable: %s %s (%s)", service, method, url, exc)
        return JSONResponse(status_code=502, content=error_body(
            "UPSTREAM_UNAVAILABLE", f"{service} is unreachable", {"service": service, "url": base_url}))

    try:
        payload = resp.json()
    except ValueError:
        payload = resp.text

    if resp.status_code >= 400:
        return JSONResponse(status_code=resp.status_code, content=_normalise_error(resp.status_code, payload, service))
    return JSONResponse(status_code=resp.status_code, content=payload)


async def get_json(url: str, timeout: float = 2.0) -> tuple[Optional[Any], Optional[str]]:
    """Best-effort GET for observability: returns (payload, error_message)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"
        return resp.json(), None
    except Exception as exc:
        return None, type(exc).__name__
