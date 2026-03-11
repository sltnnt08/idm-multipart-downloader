from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests


_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_UNTIL = 0.0


class _RateLimitedError(Exception):
    def __init__(self, retry_after_seconds: float | None) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("HTTP 429")


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    status_code: int | None
    size_bytes: int
    reason: str


class URLValidator:
    def __init__(
        self,
        timeout: int,
        min_size_bytes: int,
        verify_ssl: bool,
        retry_count: int,
        retry_backoff_seconds: float,
        head_fallback_get: bool,
        require_rar_extension: bool,
        reject_html_content: bool,
    ) -> None:
        self.timeout = timeout
        self.min_size_bytes = min_size_bytes
        self.verify_ssl = verify_ssl
        self.retry_count = retry_count
        self.retry_backoff_seconds = retry_backoff_seconds
        self.head_fallback_get = head_fallback_get
        self.require_rar_extension = require_rar_extension
        self.reject_html_content = reject_html_content
        self.session = requests.Session()

    @staticmethod
    def _parse_retry_after_seconds(retry_after_value: str | None) -> float | None:
        if not retry_after_value:
            return None

        value = retry_after_value.strip()
        if not value:
            return None

        try:
            seconds = float(value)
            return max(0.0, seconds)
        except ValueError:
            pass

        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None

        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        delta = (retry_at - now).total_seconds()
        return max(0.0, delta)

    @staticmethod
    def _apply_global_rate_limit_pause(seconds: float) -> None:
        global _RATE_LIMIT_UNTIL
        if seconds <= 0:
            return

        target = time.time() + seconds
        with _RATE_LIMIT_LOCK:
            _RATE_LIMIT_UNTIL = max(_RATE_LIMIT_UNTIL, target)

    @staticmethod
    def _wait_if_rate_limited() -> None:
        with _RATE_LIMIT_LOCK:
            wait_seconds = _RATE_LIMIT_UNTIL - time.time()

        if wait_seconds > 0:
            time.sleep(wait_seconds)

    @staticmethod
    def _is_valid_url(url: str) -> bool:
        parsed = urlparse(url)
        return bool(parsed.scheme and parsed.netloc)

    @staticmethod
    def _extract_size_from_headers(headers: requests.structures.CaseInsensitiveDict[str]) -> int:
        content_length = headers.get("Content-Length")
        if content_length is not None:
            try:
                return int(content_length)
            except (TypeError, ValueError):
                return 0

        content_range = headers.get("Content-Range", "")
        if "/" in content_range:
            total_str = content_range.rsplit("/", maxsplit=1)[-1]
            try:
                return int(total_str)
            except (TypeError, ValueError):
                return 0

        return 0

    @staticmethod
    def _extract_content_type(headers: requests.structures.CaseInsensitiveDict[str]) -> str:
        content_type = headers.get("Content-Type", "")
        return content_type.split(";", maxsplit=1)[0].strip().lower()

    @staticmethod
    def _extract_filename_from_content_disposition(
        headers: requests.structures.CaseInsensitiveDict[str],
    ) -> str:
        disposition = headers.get("Content-Disposition", "")
        if not disposition:
            return ""

        utf8_match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, flags=re.IGNORECASE)
        if utf8_match:
            return utf8_match.group(1).strip('" ').lower()

        simple_match = re.search(r'filename="?([^";]+)"?', disposition, flags=re.IGNORECASE)
        if simple_match:
            return simple_match.group(1).strip().lower()

        return ""

    def _looks_like_rar_target(
        self,
        url: str,
        headers: requests.structures.CaseInsensitiveDict[str],
    ) -> bool:
        parsed = urlparse(url)
        path_name = parsed.path.rsplit("/", maxsplit=1)[-1].lower()
        fragment_name = parsed.fragment.rsplit("/", maxsplit=1)[-1].lower()
        disposition_name = self._extract_filename_from_content_disposition(headers)
        content_type = self._extract_content_type(headers)

        filename_hints = [path_name, fragment_name, disposition_name]
        if any(name.endswith(".rar") for name in filename_hints if name):
            return True

        return content_type in {"application/x-rar-compressed", "application/vnd.rar"}

    def _validate_response(
        self,
        url: str,
        status_code: int,
        size_bytes: int,
        headers: requests.structures.CaseInsensitiveDict[str],
    ) -> ValidationResult:
        if status_code != 200 and status_code != 206:
            return ValidationResult(False, status_code, 0, f"HTTP {status_code}")

        content_type = self._extract_content_type(headers)
        if self.reject_html_content and content_type.startswith("text/html"):
            return ValidationResult(
                False,
                status_code,
                size_bytes,
                "Landing page/HTML response detected (non-direct file URL)",
            )

        if self.require_rar_extension and not self._looks_like_rar_target(url, headers):
            return ValidationResult(
                False,
                status_code,
                size_bytes,
                "Target is not recognized as RAR file",
            )

        if size_bytes < self.min_size_bytes:
            return ValidationResult(
                False,
                status_code,
                size_bytes,
                f"File too small: {size_bytes} bytes",
            )

        return ValidationResult(True, status_code, size_bytes, "OK")

    def _request_head(self, url: str) -> ValidationResult:
        self._wait_if_rate_limited()
        response = self.session.head(
            url,
            allow_redirects=True,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )

        if response.status_code == 429:
            retry_after = self._parse_retry_after_seconds(response.headers.get("Retry-After"))
            raise _RateLimitedError(retry_after)

        size_bytes = self._extract_size_from_headers(response.headers)

        if self.head_fallback_get and size_bytes == 0:
            return self._request_get_fallback(url)

        return self._validate_response(url, response.status_code, size_bytes, response.headers)

    def _request_get_fallback(self, url: str) -> ValidationResult:
        self._wait_if_rate_limited()
        response = self.session.get(
            url,
            allow_redirects=True,
            timeout=self.timeout,
            verify=self.verify_ssl,
            stream=True,
            headers={"Range": "bytes=0-0"},
        )

        if response.status_code == 429:
            retry_after = self._parse_retry_after_seconds(response.headers.get("Retry-After"))
            raise _RateLimitedError(retry_after)

        size_bytes = self._extract_size_from_headers(response.headers)
        if size_bytes <= 1:
            size_bytes = 0
        return self._validate_response(url, response.status_code, size_bytes, response.headers)

    def validate(self, url: str) -> ValidationResult:
        if not self._is_valid_url(url):
            return ValidationResult(False, None, 0, "Invalid URL format")

        attempts = self.retry_count + 1

        for attempt in range(1, attempts + 1):
            try:
                return self._request_head(url)
            except _RateLimitedError as exc:
                retry_after = exc.retry_after_seconds
                if retry_after is None:
                    retry_after = max(5.0, self.retry_backoff_seconds * (2 ** (attempt - 1)))

                self._apply_global_rate_limit_pause(retry_after)
                logging.warning(
                    "HTTP 429 for %s. Pausing %.1f seconds before retry (attempt %s/%s).",
                    url,
                    retry_after,
                    attempt,
                    attempts,
                )

                if attempt == attempts:
                    return ValidationResult(False, 429, 0, "HTTP 429 (rate limited)")
            except requests.Timeout:
                if attempt == attempts:
                    return ValidationResult(False, None, 0, "Timeout")
                time.sleep(self.retry_backoff_seconds * attempt)
            except requests.RequestException as exc:
                if attempt == attempts:
                    logging.warning("Request error when validating URL %s: %s", url, exc)
                    return ValidationResult(False, None, 0, f"Request error: {exc}")
                time.sleep(self.retry_backoff_seconds * attempt)

        return ValidationResult(False, None, 0, "Unknown validation failure")
