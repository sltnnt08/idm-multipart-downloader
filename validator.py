from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests


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
        response = self.session.head(
            url,
            allow_redirects=True,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        size_bytes = self._extract_size_from_headers(response.headers)

        if self.head_fallback_get and size_bytes == 0:
            return self._request_get_fallback(url)

        return self._validate_response(url, response.status_code, size_bytes, response.headers)

    def _request_get_fallback(self, url: str) -> ValidationResult:
        response = self.session.get(
            url,
            allow_redirects=True,
            timeout=self.timeout,
            verify=self.verify_ssl,
            stream=True,
            headers={"Range": "bytes=0-0"},
        )
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
