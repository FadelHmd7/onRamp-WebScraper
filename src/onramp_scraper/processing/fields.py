"""Site-specific fields, pulled from a page with the CSS selectors in ``sites.yaml``.

Run this on the page as fetched, **before** cleaning: cleaning drops regions
such as breadcrumbs and labels that some fields are read from.

Each rule (``FieldRule`` in ``sites.py``) gives one value:

- ``selector``            the text of the first match
- ``attr``                an attribute of the first match instead (a class list
                          is joined with spaces)
- ``many``                every match, as a list (empty and repeated values dropped)
- ``key`` + ``value``     every match becomes one pair; the field is a mapping
- ``pattern``             a regex; its first group (or the whole match) is kept
- ``type``                ``int`` or ``float``, converted after ``pattern``

A field with no match, an empty value, or a value that does not fit its
pattern or type is left out, never stored as an empty value: a listing page
simply has no ``price``.
"""

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from onramp_scraper.parsing import parse_html
from onramp_scraper.processing.text import collapse
from onramp_scraper.sites import FieldRule


def _raw(element: Tag, rule: FieldRule) -> str:
    if rule.attr is None:
        return collapse(element.get_text(" "))
    value = element.get(rule.attr)
    if isinstance(value, list):  # multi-valued attributes such as class
        value = " ".join(value)
    return collapse(str(value or ""))


def _finish(text: str, rule: FieldRule) -> str | int | float | None:
    """Apply ``pattern`` and ``type``. None means "no usable value"."""
    if rule.pattern is not None:
        match = re.search(rule.pattern, text)
        if match is None:
            return None
        text = (match.group(1) if match.groups() else match.group(0)).strip()
    if not text:
        return None
    if rule.type == "int":
        digits = text.replace(",", "")
        return int(digits) if re.fullmatch(r"[+-]?\d+", digits) else None
    if rule.type == "float":
        try:
            return float(text.replace(",", ""))
        except ValueError:
            return None
    return text


def _pairs(elements: list[Tag], key: str, value: str, rule: FieldRule) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for element in elements:
        key_tag, value_tag = element.select_one(key), element.select_one(value)
        if key_tag is None or value_tag is None:
            continue
        name = collapse(key_tag.get_text(" ")).rstrip(":").strip()
        text = _finish(collapse(value_tag.get_text(" ")), rule)
        if name and text is not None and name not in pairs:  # the first one wins
            pairs[name] = str(text)
    return pairs


def extract_field(soup: BeautifulSoup | Tag, rule: FieldRule) -> Any:
    """One field's value, or None when the page has none."""
    elements = soup.select(rule.selector)
    if rule.key is not None and rule.value is not None:
        return _pairs(elements, rule.key, rule.value, rule) or None
    if rule.many:
        values: list[Any] = []
        for element in elements:
            value = _finish(_raw(element, rule), rule)
            if value is not None and value not in values:
                values.append(value)
        return values or None
    for element in elements:  # the first match with a usable value
        value = _finish(_raw(element, rule), rule)
        if value is not None:
            return value
    return None


def extract_fields(html: str | BeautifulSoup, rules: dict[str, FieldRule]) -> dict[str, Any]:
    """Every field that has a value on this page, in the order of the rules."""
    soup = parse_html(html) if isinstance(html, str) else html
    found = {name: extract_field(soup, rule) for name, rule in rules.items()}
    return {name: value for name, value in found.items() if value is not None}
