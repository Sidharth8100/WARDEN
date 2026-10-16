"""Tests for DOM normalization, attribute canonicalization, and selector scoping."""

from warden_compare.normalize import normalize


def test_normalize_strips_scripts_styles_and_comments():
    raw_html = b"""
    <html>
        <head>
            <style>body { background: red; }</style>
            <script>alert("hello");</script>
        </head>
        <body>
            <!-- This is a comment -->
            <noscript>Enable JS</noscript>
            <h1>Main Title</h1>
            <p>Welcome to the portal.</p>
        </body>
    </html>
    """
    doc = normalize(raw_html)
    assert doc.text == "Main Title Welcome to the portal."
    assert "alert" not in doc.text
    assert "Enable JS" not in doc.text
    assert "background: red" not in doc.text


def test_normalize_strips_volatile_attributes():
    raw_html = b"""
    <html>
        <body>
            <div id="main" data-timestamp="177488392" nonce="xyz987" data-csrf-token="secret">
                <span data-user-token="abc1234">Active Account</span>
            </div>
        </body>
    </html>
    """
    doc = normalize(raw_html)
    assert doc.text == "Active Account"

    # Inspect element attributes directly on the parsed DOM
    div = doc.dom.xpath("//div[@id='main']")[0]
    assert "data-timestamp" not in div.attrib
    assert "nonce" not in div.attrib
    assert "data-csrf-token" not in div.attrib
    assert "id" in div.attrib


def test_normalize_canonicalizes_attribute_order():
    # Two elements with identical attributes in different order
    html_1 = b'<div z_attr="1" a_attr="2" m_attr="3">Content</div>'
    html_2 = b'<div a_attr="2" m_attr="3" z_attr="1">Content</div>'

    doc_1 = normalize(html_1)
    doc_2 = normalize(html_2)

    div_1 = doc_1.dom.xpath("//div")[0]
    div_2 = doc_2.dom.xpath("//div")[0]

    assert list(div_1.attrib.keys()) == ["a_attr", "m_attr", "z_attr"]
    assert list(div_2.attrib.keys()) == ["a_attr", "m_attr", "z_attr"]


def test_normalize_scopes_with_css_selector():
    raw_html = b"""
    <html>
        <body>
            <header>Site Header (Noise)</header>
            <div class="product-price">
                <span class="currency">$</span>
                <span class="amount">199.99</span>
            </div>
            <footer>Footer (Noise)</footer>
        </body>
    </html>
    """
    doc = normalize(raw_html, selector_config={"css": ".product-price"})
    assert doc.applied_selector is True
    assert doc.text == "$ 199.99"
    assert "Site Header" not in doc.text
    assert "Footer" not in doc.text


def test_normalize_scopes_with_xpath_selector():
    raw_html = b"""
    <html>
        <body>
            <div id="sidebar">Sidebar Items</div>
            <main>
                <article>Target article text</article>
            </main>
        </body>
    </html>
    """
    doc = normalize(raw_html, selector_config={"xpath": "//main/article"})
    assert doc.applied_selector is True
    assert doc.text == "Target article text"
    assert "Sidebar Items" not in doc.text


def test_normalize_empty_input():
    doc = normalize(b"")
    assert doc.text == ""
    assert doc.applied_selector is False
