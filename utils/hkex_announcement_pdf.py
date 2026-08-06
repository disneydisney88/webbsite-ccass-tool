"""Fetch and extract text from HKEX announcement PDFs.

This module deliberately accepts only HKEX announcement hosts.  It is used by
both the FastAPI route and the MCP tool, so failures are returned as structured
payloads instead of being raised through the MCP gateway.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

import requests

from utils.errors import structured_error


ALLOWED_HOSTS = frozenset({"www1.hkexnews.hk", "www.hkexnews.hk", "hkexnews.hk"})
DEFAULT_MAX_CHARS = 50_000
MAX_MAX_CHARS = 500_000
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "hkex_announcement_pdf_cache"
DATE_PATH_RE = re.compile(r"/(20\d{2})/(\d{4})(?:/|$)")
HAN_RE = re.compile(r"[\u3400-\u9fff]")


def _error_payload(
    error_code: str,
    message: str,
    *,
    url: str = "",
    status_code: int | None = None,
    final_url: str = "",
    body_head: str = "",
    data_as_of: str = "",
    data_as_of_note: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, **structured_error(error_code, message), "source": "hkexnews"}
    if url:
        payload["url"] = url
    if status_code is not None:
        payload["status_code"] = status_code
    if final_url:
        payload["final_url"] = final_url
    if body_head:
        payload["body_head"] = body_head[:500]
    if data_as_of:
        payload["data_as_of"] = data_as_of
    if data_as_of_note:
        payload["data_as_of_note"] = data_as_of_note
    return payload


def normalize_hkex_url(url: str) -> tuple[str, SplitResult] | tuple[None, None]:
    """Return a normalized, safe URL or ``(None, None)`` when it is invalid."""
    if not isinstance(url, str) or not url.strip():
        return None, None
    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        _ = parsed.port
    except ValueError:
        return None, None
    if parsed.scheme.lower() != "https" or host not in ALLOWED_HOSTS:
        return None, None
    if parsed.port not in (None, 443):
        return None, None
    if parsed.username is not None or parsed.password is not None:
        return None, None
    normalized = urlunsplit(
        (
            "https",
            host,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )
    return normalized, parsed


def _data_as_of(url: str) -> tuple[str, str]:
    match = DATE_PATH_RE.search(urlsplit(url).path)
    if match:
        year, month_day = match.groups()
        try:
            parsed_date = date(int(year), int(month_day[:2]), int(month_day[2:]))
        except ValueError:
            parsed_date = None
        if parsed_date:
            return parsed_date.isoformat(), "Inferred from the HKEX URL path."
    return date.today().isoformat(), "The URL did not contain an HKEX publication date; using the fetch date."


def _cache_dir() -> Path:
    configured = os.getenv("HKEX_PDF_CACHE_DIR", "").strip()
    return Path(configured) if configured else DEFAULT_CACHE_DIR


def _cache_path(normalized_url: str) -> Path:
    digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
    return _cache_dir() / f"{digest}.json"


def _shape_success_payload(payload: dict[str, Any], max_chars: int, *, cached: bool) -> dict[str, Any]:
    shaped = dict(payload)
    full_text = str(payload.get("text") or "")
    chars_total = len(full_text)
    shaped["chars_total"] = chars_total
    shaped["chars_returned"] = min(chars_total, max_chars)
    shaped["truncated"] = chars_total > max_chars
    shaped["cached"] = cached
    shaped["text"] = full_text[:max_chars]
    return shaped


def _read_cached(normalized_url: str, max_chars: int) -> dict[str, Any] | None:
    path = _cache_path(normalized_url)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("ok") is not True or not payload.get("text"):
        return None
    return _shape_success_payload(payload, max_chars, cached=True)


def _write_cached(normalized_url: str, payload: dict[str, Any]) -> None:
    directory = _cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = _cache_path(normalized_url)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f"{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(payload, temporary, ensure_ascii=False)
        Path(temporary_name).replace(path)
    except OSError:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def _body_head(content: bytes) -> str:
    return content[:500].decode("utf-8", errors="replace").replace("\x00", " ").strip()


def _rawdict_text(page: Any) -> str:
    raw = page.get_text("rawdict")
    lines: list[str] = []
    for block in raw.get("blocks", []) if isinstance(raw, dict) else []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans: list[str] = []
            for span in line.get("spans", []):
                text = span.get("text") or ""
                if not text:
                    text = "".join(str(char.get("c") or "") for char in span.get("chars", []))
                spans.append(text)
            if spans:
                lines.append("".join(spans))
    return "\n".join(lines)


def _blocks_text(page: Any) -> str:
    blocks = page.get_text("blocks")
    return "\n".join(str(block[4]) for block in blocks if len(block) > 4 and block[4])


def _clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\ufffd", "")
    return "\n".join(line.rstrip() for line in text.replace("\r", "").splitlines()).strip()


def _text_quality(text: str) -> tuple[int, int, int]:
    visible = "".join(text.split())
    han_count = len(HAN_RE.findall(visible))
    alnum_count = sum(char.isalnum() for char in visible)
    replacement_count = text.count("\ufffd")
    return (han_count * 10 + alnum_count - replacement_count * 20, len(visible), -replacement_count)


def _extract_page_text(page: Any) -> str:
    candidates: list[str] = []
    for mode, extractor in (
        ("text", lambda: page.get_text("text")),
        ("blocks", lambda: _blocks_text(page)),
        ("rawdict", lambda: _rawdict_text(page)),
    ):
        try:
            candidate = _clean_text(str(extractor() or ""))
        except Exception:
            candidate = ""
        if candidate:
            candidates.append(candidate)
    if not candidates:
        return ""
    return max(candidates, key=_text_quality)


def _extract_pdf_text(content: bytes) -> tuple[int, str]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required for HKEX PDF text extraction.") from exc

    document = fitz.open(stream=content, filetype="pdf")
    try:
        page_texts = [_extract_page_text(page) for page in document]
        return document.page_count, _clean_text("\n\n".join(text for text in page_texts if text))
    finally:
        document.close()


def fetch_announcement_pdf(
    url: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Fetch one HKEX announcement PDF and return readable extracted text."""
    normalized_url, _ = normalize_hkex_url(url)
    if not normalized_url:
        return _error_payload(
            "URL_NOT_ALLOWED",
            "Only https hkexnews.hk URLs without credentials are accepted.",
            url=str(url or ""),
        )
    data_as_of, data_as_of_note = _data_as_of(normalized_url)
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 1 <= max_chars <= MAX_MAX_CHARS:
        return _error_payload(
            "INVALID_MAX_CHARS",
            f"max_chars must be an integer from 1 to {MAX_MAX_CHARS}.",
            url=normalized_url,
            data_as_of=data_as_of,
            data_as_of_note=data_as_of_note,
        )

    cached = _read_cached(normalized_url, max_chars)
    if cached:
        return cached

    try:
        response = requests.get(
            normalized_url,
            headers={
                "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
                "User-Agent": "webbsite-ccass-api announcement fetcher/1.0",
            },
            allow_redirects=False,
            timeout=(5, max(1.0, min(float(timeout), 15.0))),
        )
    except requests.Timeout:
        return _error_payload(
            "SOURCE_TIMEOUT",
            "HKEX announcement PDF request timed out.",
            url=normalized_url,
            data_as_of=data_as_of,
            data_as_of_note=data_as_of_note,
        )
    except requests.RequestException as exc:
        return _error_payload(
            "SOURCE_FETCH_FAILED",
            f"HKEX announcement PDF request failed: {exc}",
            url=normalized_url,
            data_as_of=data_as_of,
            data_as_of_note=data_as_of_note,
        )

    final_url = str(getattr(response, "url", "") or normalized_url)
    if response.status_code >= 400:
        return _error_payload(
            "SOURCE_FETCH_FAILED",
            f"HKEX announcement PDF returned HTTP {response.status_code} {response.reason or ''}".strip(),
            url=normalized_url,
            status_code=response.status_code,
            final_url=final_url,
            body_head=_body_head(response.content),
            data_as_of=data_as_of,
            data_as_of_note=data_as_of_note,
        )
    if not response.content.startswith(b"%PDF-"):
        return _error_payload(
            "UPSTREAM_NOT_PDF",
            "HKEX returned a non-PDF response; the announcement may require a different URL or be unavailable.",
            url=normalized_url,
            status_code=response.status_code,
            final_url=final_url,
            body_head=_body_head(response.content),
            data_as_of=data_as_of,
            data_as_of_note=data_as_of_note,
        )

    try:
        pages_total, text = _extract_pdf_text(response.content)
    except RuntimeError as exc:
        return _error_payload(
            "PDF_DEPENDENCY_MISSING",
            str(exc),
            url=normalized_url,
            status_code=response.status_code,
            final_url=final_url,
            data_as_of=data_as_of,
            data_as_of_note=data_as_of_note,
        )
    except Exception as exc:
        return _error_payload(
            "PDF_PARSE_ERROR",
            f"HKEX PDF could not be opened or parsed: {exc}",
            url=normalized_url,
            status_code=response.status_code,
            final_url=final_url,
            data_as_of=data_as_of,
            data_as_of_note=data_as_of_note,
        )

    if not text:
        return _error_payload(
            "PDF_TEXT_EXTRACTION_FAILED",
            "HKEX PDF opened successfully but contained no extractable text; OCR is not enabled.",
            url=normalized_url,
            status_code=response.status_code,
            final_url=final_url,
            data_as_of=data_as_of,
            data_as_of_note=data_as_of_note,
        )

    chars_total = len(text)
    full_payload: dict[str, Any] = {
        "ok": True,
        "source": "hkexnews",
        "data_as_of": data_as_of,
        "data_as_of_note": data_as_of_note,
        "url": normalized_url,
        "pages_total": pages_total,
        "chars_total": chars_total,
        "chars_returned": chars_total,
        "truncated": False,
        "cached": False,
        "text": text,
    }
    _write_cached(normalized_url, full_payload)
    return _shape_success_payload(full_payload, max_chars, cached=False)
