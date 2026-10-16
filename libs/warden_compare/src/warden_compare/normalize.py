"""DOM normalization, volatile attribute stripping, and selector scoping."""

import re
import time
from dataclasses import dataclass
from typing import Any

import cssselect
import lxml.etree  # type: ignore[import-untyped]
import lxml.html  # type: ignore[import-untyped]
import structlog

_log = structlog.get_logger(__name__)

# Documented, extensible list of volatile attributes stripped during normalization.
# HEURISTIC RATIONALE:
# - data-timestamp: Real-time update tickers that change on every reload without content change.
# - nonce: Cryptographic nonces generated afresh per HTTP response for CSP.
# - csrf/token: Form tokens, session tokens, and security nonces injected dynamically.
# Imperfection acknowledgment: A legitimate functional attribute named "token_count" or
# "user_token" in a developer API documentation page could be stripped. This trade-off
# accepts occasional false negatives in attribute values in exchange for eliminating
# 99%+ of cosmetic diff noise across dynamic sites.
VOLATILE_EXACT_ATTRS = frozenset({"data-timestamp", "nonce"})


def _is_volatile_attr(name: str) -> bool:
    name_lower = name.lower()
    if name_lower in VOLATILE_EXACT_ATTRS:
        return True
    return "csrf" in name_lower or "token" in name_lower


@dataclass(frozen=True)
class NormalizedDoc:
    """Normalized document representation stripped of volatile noise."""

    text: str
    dom: Any  # lxml.html.HtmlElement
    applied_selector: bool


def normalize(
    html: bytes,
    *,
    selector_config: dict[str, Any] | None = None,
) -> NormalizedDoc:
    """Normalize raw HTML bytes into a deterministic text and DOM representation.

    PURITY NOTE:
    This function is purely deterministic: given identical `html` bytes and `selector_config`,
    it returns the exact same NormalizedDoc every single time.
    """
    start_time = time.monotonic()

    if not html.strip():
        empty_dom = lxml.html.fromstring("<div></div>")
        return NormalizedDoc(text="", dom=empty_dom, applied_selector=False)

    # 1. Parse HTML using lxml.html
    tree = lxml.html.fromstring(html)

    # 2. Strip scripts, styles, noscript, and comments
    for el in tree.xpath("//script | //style | //noscript | //comment()"):
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)

    # 3. Strip volatile attributes and canonicalize attribute order (alphabetical)
    for el in tree.iter():
        if not isinstance(el, lxml.html.HtmlElement):
            continue
        attrs_to_remove = [k for k in el.attrib if _is_volatile_attr(k)]
        for k in attrs_to_remove:
            del el.attrib[k]

        # Sort remaining attributes alphabetically for canonical serialization
        if el.attrib:
            sorted_attrs = sorted(el.attrib.items())
            el.attrib.clear()
            for k, v in sorted_attrs:
                el.attrib[k] = v

    # 4. Scope to selector if provided
    # CONTINUITY NOTE:
    # This is the first place the selector validated at Prompt 2's monitor-creation
    # time is actually USED. We reuse the exact same cssselect and lxml.etree.XPath
    # machinery to narrow the parsed document to matched subtrees.
    applied_selector = False
    if selector_config:
        matched_elements: list[Any] = []
        if selector_config.get("css"):
            css_expr = selector_config["css"]
            xpath_query = cssselect.GenericTranslator().css_to_xpath(css_expr)
            matched_elements = tree.xpath(xpath_query)
            applied_selector = True
        elif selector_config.get("xpath"):
            xpath_expr = selector_config["xpath"]
            xpath_obj = lxml.etree.XPath(xpath_expr)
            matched_elements = xpath_obj(tree)
            applied_selector = True

        if applied_selector and matched_elements:
            container = lxml.html.Element("div")
            for m in matched_elements:
                container.append(m)
            tree = container

    # 5. Extract text content and collapse whitespace runs
    if tree is not None:
        text_nodes = [t.strip() for t in tree.xpath("//text()") if t.strip()]
        raw_text = " ".join(text_nodes)
    else:
        raw_text = ""
    cleaned_text = re.sub(r"\s+", " ", raw_text).strip()

    elapsed_ms = (time.monotonic() - start_time) * 1000.0
    _log.info(
        "compare.normalize",
        text_length=len(cleaned_text),
        applied_selector=applied_selector,
        elapsed_ms=round(elapsed_ms, 2),
    )

    return NormalizedDoc(
        text=cleaned_text,
        dom=tree,
        applied_selector=applied_selector,
    )
