from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests


@dataclass(frozen=True)
class ResolveResult:
    resolved_url: str
    was_resolved: bool
    reason: str


_WINDOW_OPEN_RE = re.compile(r"window\.open\(\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
_HREF_DL_RE = re.compile(r"href\s*=\s*['\"]([^'\"]*/dl/[^'\"]*)['\"]", re.IGNORECASE)


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme in {"http", "https"} and parsed.netloc)


def _resolve_with_selenium_click(
    url: str,
    timeout: int,
    headless: bool,
) -> ResolveResult:
    try:
        from selenium import webdriver
        from selenium.common.exceptions import TimeoutException, WebDriverException
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError:
        return ResolveResult(
            resolved_url=url,
            was_resolved=False,
            reason="selenium not installed",
        )

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(timeout)
        driver.get(url)

        wait = WebDriverWait(driver, timeout)
        button = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[contains(translate(normalize-space(.), 'download', 'DOWNLOAD'), 'DOWNLOAD')]",
                )
            )
        )

        existing_handles = set(driver.window_handles)
        button.click()

        end_time = time.time() + timeout
        while time.time() < end_time:
            current_handles = set(driver.window_handles)
            new_handles = current_handles - existing_handles
            if new_handles:
                handle = next(iter(new_handles))
                driver.switch_to.window(handle)
                candidate_url = driver.current_url
                if _is_http_url(candidate_url):
                    return ResolveResult(
                        resolved_url=candidate_url,
                        was_resolved=True,
                        reason="resolved via selenium click",
                    )

            candidate_url = driver.current_url
            if "/dl/" in urlparse(candidate_url).path and _is_http_url(candidate_url):
                return ResolveResult(
                    resolved_url=candidate_url,
                    was_resolved=True,
                    reason="resolved via selenium same-tab",
                )

            time.sleep(0.2)

        return ResolveResult(
            resolved_url=url,
            was_resolved=False,
            reason="selenium click did not produce direct link",
        )
    except (TimeoutException, WebDriverException) as exc:
        return ResolveResult(
            resolved_url=url,
            was_resolved=False,
            reason=f"selenium fallback error: {exc}",
        )
    finally:
        if driver is not None:
            driver.quit()


def resolve_download_button_link(
    url: str,
    timeout: int,
    verify_ssl: bool,
    enabled: bool,
    selenium_fallback_enabled: bool,
    selenium_headless: bool,
) -> ResolveResult:
    if not enabled:
        return ResolveResult(resolved_url=url, was_resolved=False, reason="resolver disabled")

    parsed = urlparse(url)
    if "/dl/" in parsed.path:
        return ResolveResult(resolved_url=url, was_resolved=False, reason="already direct")

    try:
        response = requests.get(
            url,
            allow_redirects=True,
            timeout=timeout,
            verify=verify_ssl,
        )
    except requests.RequestException as exc:
        return ResolveResult(resolved_url=url, was_resolved=False, reason=f"resolver request error: {exc}")

    content_type = (response.headers.get("Content-Type") or "").split(";", maxsplit=1)[0].strip().lower()
    if not content_type.startswith("text/html"):
        return ResolveResult(resolved_url=url, was_resolved=False, reason="not an HTML landing page")

    html = response.text or ""

    open_matches = _WINDOW_OPEN_RE.findall(html)
    for candidate in open_matches:
        direct_url = urljoin(response.url, candidate)
        if _is_http_url(direct_url):
            return ResolveResult(resolved_url=direct_url, was_resolved=True, reason="resolved via window.open")

    href_matches = _HREF_DL_RE.findall(html)
    for candidate in href_matches:
        direct_url = urljoin(response.url, candidate)
        if _is_http_url(direct_url):
            return ResolveResult(resolved_url=direct_url, was_resolved=True, reason="resolved via href /dl/")

    if selenium_fallback_enabled:
        selenium_result = _resolve_with_selenium_click(
            url=url,
            timeout=timeout,
            headless=selenium_headless,
        )
        if selenium_result.was_resolved:
            return selenium_result
        return ResolveResult(
            resolved_url=url,
            was_resolved=False,
            reason=f"download button link not found; {selenium_result.reason}",
        )

    return ResolveResult(resolved_url=url, was_resolved=False, reason="download button link not found")
