"""Explicit single-value price extraction; pure and network-free."""

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

import cssselect

from warden_compare.normalize import NormalizedDoc


def extract_price(document: NormalizedDoc, configuration: Mapping[str, object]) -> Decimal | None:
    selector = configuration.get("price_css")
    if not isinstance(selector, str) or not 1 <= len(selector) <= 512:
        return None
    currency = configuration.get("price_currency")
    decimal = configuration.get("price_decimal_separator", ".")
    grouping = configuration.get("price_group_separator", ",")
    if not isinstance(currency, str) or len(currency) != 3 or decimal not in (".", ","):
        return None
    if grouping not in ("", ",", ".", " ") or grouping == decimal:
        return None
    try:
        query = cssselect.GenericTranslator().css_to_xpath(selector)
        selected = document.dom.xpath(query)
        if len(selected) != 1:
            return None
        raw = "".join(selected[0].itertext()).strip()
        for symbol in ("$", "€", "£", "₹", currency):
            raw = raw.replace(symbol, "")
        raw = raw.strip()
        if not raw or len(raw) > 40:
            return None
        if grouping:
            integer = raw.split(str(decimal), 1)[0]
            groups = integer.split(str(grouping))
            if len(groups) > 1 and (
                not 1 <= len(groups[0]) <= 3 or any(len(x) != 3 for x in groups[1:])
            ):
                return None
            raw = raw.replace(str(grouping), "")
        raw = raw.replace(str(decimal), ".")
        if any(c not in "0123456789." for c in raw) or raw.count(".") > 1:
            return None
        value = Decimal(raw)
        return value if value.is_finite() and 0 <= value <= Decimal("1e15") else None
    except (ValueError, InvalidOperation, cssselect.SelectorError, AttributeError):
        return None
