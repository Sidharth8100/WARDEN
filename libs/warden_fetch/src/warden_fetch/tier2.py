"""Tier-2 Selenium Grid browser fetcher with headless Chrome and CDP integration."""

import asyncio
import base64
import contextlib
import time
from dataclasses import dataclass

import structlog
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

from warden_net.ssrf_check import SSRFBlockedError, validate_url_is_safe

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Tier2Result:
    """Outcome of a Tier-2 Selenium Grid browser fetch."""

    status: int
    final_url: str
    headers: dict[str, str]
    body: bytes
    not_modified: bool
    elapsed_ms: float
    screenshot: bytes
    rendered_html: bytes


def _run_selenium_session(
    target_url: str,
    selenium_hub_url: str,
    timeout_s: float,
    max_bytes: int,
) -> tuple[int, str, dict[str, str], bytes, bytes]:
    """Execute synchronous Selenium actions inside a dedicated thread.

    WHY ASYNCIO.TO_THREAD IS REQUIRED:
    Selenium's Python client uses blocking synchronous HTTP calls (via urllib3) over
    the WebDriver Wire Protocol / W3C WebDriver spec. Calling blocking Selenium driver
    methods directly inside an `async def` would block the Python process's main event
    loop, starving all other concurrent requests in the application. Running the session
    in an OS thread pool via `asyncio.to_thread` isolates the blocking I/O.
    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Remote(
        command_executor=selenium_hub_url,
        options=options,
    )

    try:
        # Set internal driver timeouts shorter than outer asyncio.wait_for budget
        internal_timeout = max(1.0, timeout_s - 2.0)
        driver.set_page_load_timeout(internal_timeout)
        driver.set_script_timeout(internal_timeout)

        # CDP optimization: Block image, font, and media loading to accelerate rendering
        with contextlib.suppress(WebDriverException):
            driver.execute_cdp_cmd(
                "Network.setBlockedURLs",
                {
                    "urls": [
                        "*.png",
                        "*.jpg",
                        "*.jpeg",
                        "*.gif",
                        "*.webp",
                        "*.svg",
                        "*.woff",
                        "*.woff2",
                        "*.ttf",
                        "*.eot",
                        "*.mp4",
                        "*.webm",
                    ]
                },
            )

        # Navigate to target URL
        driver.get(target_url)

        # Wait for document ready state + short settle delay
        # CDP network idle listener is explicitly out of scope for Prompt 3
        driver.execute_script("return document.readyState === 'complete'")
        time.sleep(0.5)

        final_url = driver.current_url
        page_source = driver.page_source.encode("utf-8")
        if len(page_source) > max_bytes:
            page_source = page_source[:max_bytes]

        # Full-page screenshot via CDP captureBeyondViewport
        screenshot_bytes = b""
        with contextlib.suppress(Exception):
            screenshot_cdp = driver.execute_cdp_cmd(
                "Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": True},
            )
            screenshot_bytes = base64.b64decode(screenshot_cdp["data"])

        if not screenshot_bytes:
            with contextlib.suppress(Exception):
                screenshot_bytes = driver.get_screenshot_as_png()

        return 200, final_url, {}, page_source, screenshot_bytes

    finally:
        # GUARANTEE SESSION CLEANUP:
        # Closing sessions in `finally` prevents orphaned Chrome containers from leaking memory.
        with contextlib.suppress(Exception):
            driver.quit()


async def fetch_tier2(
    url: str,
    *,
    max_bytes: int = 10_000_000,
    timeout_s: float = 15.0,
    selenium_hub_url: str = "http://selenium-hub:4444",
    extra_allowed_hosts: frozenset[str] = frozenset(),
) -> Tier2Result:
    """Safely fetch a JavaScript-rendered page via headless Chrome on Selenium Grid."""
    start_time = time.monotonic()

    # DEFENSE IN DEPTH: Re-run SSRF validation before creating browser session
    check = await validate_url_is_safe(url, extra_allowed_hosts=extra_allowed_hosts)
    if not check.safe:
        raise SSRFBlockedError(f"Tier-2 SSRF check rejected target URL ({url}): {check.reason}")

    # Run blocking Selenium driver calls in thread with outer asyncio.wait_for enforcement
    status, final_url, headers, page_source, screenshot = await asyncio.wait_for(
        asyncio.to_thread(
            _run_selenium_session,
            url,
            selenium_hub_url,
            timeout_s,
            max_bytes,
        ),
        timeout=timeout_s,
    )

    elapsed_ms = (time.monotonic() - start_time) * 1000.0

    _log.info(
        "fetch.tier2",
        url=url,
        status=status,
        rendered_bytes=len(page_source),
        screenshot_bytes=len(screenshot),
        elapsed_ms=round(elapsed_ms, 2),
    )

    return Tier2Result(
        status=status,
        final_url=final_url,
        headers=headers,
        body=page_source,
        not_modified=False,
        elapsed_ms=elapsed_ms,
        screenshot=screenshot,
        rendered_html=page_source,
    )
