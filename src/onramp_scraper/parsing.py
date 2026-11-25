"""One way to parse fetched HTML, shared by link extraction, cleaning and fields."""

import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning


def parse_html(html: str) -> BeautifulSoup:
    """Parse with lxml's HTML parser, the way a browser reads the page.

    Some sites (Standard Ebooks) serve XHTML with an ``<?xml ...?>`` line.
    Browsers still read it as HTML, and so do we, so bs4's warning about it
    is silenced here.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        return BeautifulSoup(html, "lxml")
