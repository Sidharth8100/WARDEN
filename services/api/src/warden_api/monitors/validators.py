"""Validation helpers for monitor selector configurations and intervals."""

import re
from collections.abc import Mapping
from typing import Any

import cssselect
import lxml.etree  # type: ignore[import-untyped]
from cssselect.parser import SelectorError

from warden_api.errors import APIError


def validate_selector_config(config: Mapping[str, Any] | None) -> None:
    """Validate optional selector_config dictionary containing css, xpath, and/or regex keys.

    Any syntax or compilation failure raises an RFC 7807 422 APIError specifying the key and reason.
    """
    if not config:
        return

    # 1. CSS Selector validation
    css_val = config.get("css")
    if css_val is not None:
        if not isinstance(css_val, str) or not css_val.strip():
            raise APIError(
                status_code=422,
                title="Unprocessable Entity",
                detail="CSS selector must be a non-empty string",
            )
        try:
            cssselect.GenericTranslator().css_to_xpath(css_val)
        except (SelectorError, Exception) as exc:
            raise APIError(
                status_code=422,
                title="Unprocessable Entity",
                detail=f"Invalid CSS selector in selector_config.css: {exc}",
            ) from exc

    # 2. XPath Expression validation
    xpath_val = config.get("xpath")
    if xpath_val is not None:
        if not isinstance(xpath_val, str) or not xpath_val.strip():
            raise APIError(
                status_code=422,
                title="Unprocessable Entity",
                detail="XPath expression must be a non-empty string",
            )
        try:
            lxml.etree.XPath(xpath_val)
        except (lxml.etree.XPathSyntaxError, lxml.etree.XPathError, Exception) as exc:
            raise APIError(
                status_code=422,
                title="Unprocessable Entity",
                detail=f"Invalid XPath expression in selector_config.xpath: {exc}",
            ) from exc

    # 3. Regular Expression validation
    regex_val = config.get("regex")
    if regex_val is not None:
        if not isinstance(regex_val, str) or not regex_val.strip():
            raise APIError(
                status_code=422,
                title="Unprocessable Entity",
                detail="Regex pattern must be a non-empty string",
            )
        try:
            # NOTE: ReDoS-safe regex evaluation via Google RE2 is a Day-16 concern.
            # Here we validate syntax compilation using Python's standard re module.
            re.compile(regex_val)
        except re.error as exc:
            raise APIError(
                status_code=422,
                title="Unprocessable Entity",
                detail=f"Invalid regex pattern in selector_config.regex: {exc}",
            ) from exc
