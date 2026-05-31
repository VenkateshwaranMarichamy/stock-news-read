"""HTML parsing for MoneyControl 'Stocks to Watch' articles."""

import json
import logging
import re
from typing import Iterator

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from moneycontrol_scraper.models import OutputRecord

logger = logging.getLogger(__name__)

# Ordered list of CSS selectors to try when locating the primary content container.
# arti-flow is tried first as it is the tightest article body container on MoneyControl.
_CONTENT_SELECTORS = [
    "div.arti-flow",
    "div.article-desc",
    "div#article-main",
    "div.content_wrapper",
]

# Heading tag names treated as section-level headings.
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class ArticleParser:
    """Parse a MoneyControl article page into an OutputRecord."""

    # ------------------------------------------------------------------
    # Public API (stubs for tasks not yet implemented)
    # ------------------------------------------------------------------

    def parse(self, html: str, url: str) -> OutputRecord:
        """Parse a MoneyControl article page.

        Args:
            html: Raw HTML string of the article page.
            url:  The source URL (used for date fallback and warnings).

        Returns:
            An OutputRecord dataclass instance.
        """
        # NOTE: _extract_date and full wiring are implemented in tasks 3.6 / 3.9.
        soup = BeautifulSoup(html, "html.parser")
        container = self._find_content_container(soup)
        sections = self._extract_sections(container, url)
        date = self._extract_date(soup, url)
        return OutputRecord(date=date, url=url, sections=sections)

    def _extract_date(self, soup: BeautifulSoup, url: str) -> str | None:
        """Extract publication date using a five-priority chain.

        Priority order:
        1. JSON-LD ``datePublished``
        2. ``<meta property="article:published_time">`` or
           ``<meta property="og:article:published_time">``
        3. ``<meta name="publish-date">`` or ``<time datetime>`` element
        4. URL path regex: ``YYYY/MM/DD`` or ``YYYY-MM-DD``
        5. Return ``None`` and emit ``logging.warning``

        All extracted dates are normalised to ``YYYY-MM-DD``.
        """
        # ------------------------------------------------------------------
        # Priority 1: JSON-LD datePublished
        # ------------------------------------------------------------------
        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.string or ""
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue

            # data may be a dict or a list of dicts
            candidates = data if isinstance(data, list) else [data]
            for obj in candidates:
                if not isinstance(obj, dict):
                    continue
                date_str = obj.get("datePublished")
                if date_str:
                    normalised = _normalise_date(str(date_str))
                    if normalised:
                        return normalised

        # ------------------------------------------------------------------
        # Priority 2: <meta property="article:published_time"> or
        #             <meta property="og:article:published_time">
        # ------------------------------------------------------------------
        for prop_value in ("article:published_time", "og:article:published_time"):
            meta = soup.find("meta", property=prop_value)
            if meta and meta.get("content"):
                normalised = _normalise_date(str(meta["content"]))
                if normalised:
                    return normalised

        # ------------------------------------------------------------------
        # Priority 3: <meta name="publish-date"> or <time datetime>
        # ------------------------------------------------------------------
        meta_publish = soup.find("meta", attrs={"name": "publish-date"})
        if meta_publish and meta_publish.get("content"):
            normalised = _normalise_date(str(meta_publish["content"]))
            if normalised:
                return normalised

        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag and time_tag.get("datetime"):
            normalised = _normalise_date(str(time_tag["datetime"]))
            if normalised:
                return normalised

        # ------------------------------------------------------------------
        # Priority 4: URL path regex YYYY/MM/DD or YYYY-MM-DD
        # ------------------------------------------------------------------
        match = re.search(r"(\d{4})[/\-](\d{2})[/\-](\d{2})", url)
        if match:
            year, month, day = match.group(1), match.group(2), match.group(3)
            return f"{year}-{month}-{day}"

        # ------------------------------------------------------------------
        # Priority 5: give up
        # ------------------------------------------------------------------
        logger.warning("Could not extract publication date from article: %s", url)
        return None

    # ------------------------------------------------------------------
    # Task 3.1 — content container + section extraction
    # ------------------------------------------------------------------

    def _find_content_container(self, soup: BeautifulSoup) -> Tag:
        """Locate the primary article content div.

        Tries each selector in ``_CONTENT_SELECTORS`` in order and returns
        the first match.  Falls back to ``<body>`` if none match, and to the
        root ``soup`` object itself if there is no ``<body>`` tag.

        Also removes known widget/sidebar containers that embed junk headings
        (e.g. stock ticker widgets, related-stories blocks) so they are not
        mistaken for article sections.
        """
        for selector in _CONTENT_SELECTORS:
            tag, _, css_class = selector.partition(".")
            if css_class:
                found = soup.find(tag, class_=css_class)
            else:
                # Handle id-based selectors like div#article-main
                tag_part, _, id_part = selector.partition("#")
                if id_part:
                    found = soup.find(tag_part, id=id_part)
                else:
                    found = soup.find(tag)

            if found is not None:
                # Remove embedded widget/sidebar divs that contain junk headings.
                _remove_noise_elements(found)
                return found  # type: ignore[return-value]

        # Fall back to <body>, then to the soup root.
        body = soup.find("body")
        if body is not None:
            _remove_noise_elements(body)
            return body  # type: ignore[return-value]
        return soup  # type: ignore[return-value]

    def _extract_sections(
        self, container: Tag, url: str
    ) -> dict[str, dict[str, str]]:
        """Walk the container's children and build the sections dict.

        Algorithm (state machine):
          Scanning  → InSection  on a section heading (non-empty text)
          InSection → InStock    on a stock sub-heading (link or bold/strong)
          InStock   → InStock    on a paragraph (append text to current stock)
          InStock   → InStock    on another stock heading (save, start new)
          InStock   → InSection  on a section heading (save stock, new section)

        Returns:
            A dict mapping section title → {stock name → news text}.
            Returns ``{}`` and logs a warning when no headings are found.
        """
        sections: dict[str, dict[str, str]] = {}

        # State
        current_section: str | None = None
        current_stock: str | None = None
        current_paragraphs: list[str] = []

        def _flush_stock() -> None:
            """Save the accumulated paragraphs for the current stock."""
            nonlocal current_stock, current_paragraphs
            if current_section is not None and current_stock is not None:
                text = " ".join(p for p in current_paragraphs if p)
                text = text.strip()
                existing = sections[current_section].get(current_stock, "")
                if existing:
                    sections[current_section][current_stock] = (
                        (existing + " " + text).strip() if text else existing
                    )
                else:
                    sections[current_section][current_stock] = text
            current_stock = None
            current_paragraphs = []

        def _flush_section() -> None:
            """Flush the current stock and reset section state."""
            _flush_stock()
            nonlocal current_section
            current_section = None

        # Iterate over all descendants in document order, but only process
        # direct-ish block elements to avoid double-counting nested content.
        # We use a flat walk of the container's *recursive* children but skip
        # elements whose parent is already a processed block so we don't
        # re-process nested paragraphs.
        for element in _iter_block_elements(container):
            if not isinstance(element, Tag):
                continue

            tag_name = element.name.lower() if element.name else ""

            # ---- Case 1: combo paragraph — section label + first stock in same <p> ----
            # Pattern: <p><strong>Section Label</strong><a href="...">Stock</a>text</p>
            # The <strong> has no link, but the <p> also contains an <a> tag.
            # We split this into: start a new section, then treat the <a>+text as a stock entry.
            if tag_name == "p":
                combo = _split_combo_paragraph(element)
                if combo is not None:
                    section_label, stock_name, stock_text = combo
                    # Flush any in-flight stock from the previous section
                    _flush_stock()
                    current_section = section_label
                    current_stock = None
                    current_paragraphs = []
                    if current_section not in sections:
                        sections[current_section] = {}
                    # Now record the first stock entry
                    current_stock = stock_name
                    sections[current_section][current_stock] = ""
                    if stock_text:
                        current_paragraphs = [stock_text]
                    continue

            # ---- Stock sub-heading? (only meaningful inside a section) -----
            # Check stock heading BEFORE section heading when inside a section,
            # so that heading tags (h3–h6) containing a link are treated as
            # stock entries rather than new top-level sections (Req 3.1).
            if current_section is not None and _is_stock_heading(element):
                stock_name = _get_stock_name(element).strip()
                if not stock_name:
                    continue

                _flush_stock()
                current_stock = stock_name
                current_paragraphs = []
                # Ensure the stock key exists (Req 3.6 — empty string default)
                if current_stock not in sections[current_section]:
                    sections[current_section][current_stock] = ""

                # Capture any inline text that follows the <a> tag within the
                # same <p> (e.g. <p><a>Stock:</a> news text here</p>)
                inline_text = _get_inline_text_after_link(element)
                if inline_text:
                    current_paragraphs = [inline_text]
                continue

            # ---- Section heading? ----------------------------------------
            if _is_section_heading(element):
                heading_text = _get_text(element).strip()
                if not heading_text:
                    continue  # skip empty/whitespace-only headings (Req 2.2)

                _flush_stock()
                current_section = heading_text
                current_stock = None
                current_paragraphs = []
                if current_section not in sections:
                    sections[current_section] = {}
                continue

            # ---- Paragraph inside a stock entry ---------------------------
            if (
                current_section is not None
                and current_stock is not None
                and tag_name == "p"
            ):
                para_text = _get_text(element).strip()
                if para_text:
                    current_paragraphs.append(para_text)
                continue

        # Flush whatever is still in flight at end of document.
        _flush_stock()

        # Post-process: sections whose content is only whitespace or HTML
        # comments should be represented as {} (Req 3.5).
        for sec_title in list(sections.keys()):
            stock_dict = sections[sec_title]
            # Remove entries that are purely whitespace (shouldn't happen after
            # strip, but guard anyway).
            cleaned = {k: v for k, v in stock_dict.items() if k.strip()}
            sections[sec_title] = cleaned

        if not sections:
            logger.warning("No sections found in article: %s", url)
            return {}

        return sections


# ---------------------------------------------------------------------------
# Noise removal — strip widget/sidebar elements before parsing
# ---------------------------------------------------------------------------

# CSS classes of container elements that embed junk headings (stock tickers,
# related-stories widgets, social share bars, etc.) and should be removed
# before section extraction.
_NOISE_CLASSES = {
    "stockwidget_container_new",
    "stockwgblock_new",
    "strArtcl",          # "Related Stories" block
    "social_icons_wrapper",
    "social_icons_list",
    "articleRHS",
    "Rhs_content",
    "article_schedule",
    "article_image_wrapper",
    "article_image_main_wrapper",
    "article_author",
    "articlename_join_follow",
    "expandDetails",
}


def _remove_noise_elements(container: Tag) -> None:
    """Decompose known widget/sidebar elements from *container* in-place.

    This prevents stock-ticker widgets, related-stories blocks, and other
    embedded UI components from contributing junk headings to the parsed
    sections dict.
    """
    for noise_class in _NOISE_CLASSES:
        for el in container.find_all(True, class_=noise_class):
            el.decompose()


def _split_combo_paragraph(element: Tag) -> tuple[str, str, str] | None:
    """Detect and split a "combo paragraph" that contains both a section label
    and the first stock entry in the same ``<p>`` tag.

    MoneyControl's "Stocks in news" articles sometimes use this pattern:

        <p>
          <strong>Stocks in news </strong>
          <a href="...">Biocon Limited:</a>
          The company has denied reports...
        </p>

    This function returns ``(section_label, stock_name, stock_text)`` when the
    pattern is detected, or ``None`` otherwise.

    Detection criteria:
    - The element is a ``<p>`` tag.
    - Its first Tag child is a ``<strong>`` or ``<b>`` that does NOT contain
      an ``<a>`` tag (i.e. it is a section label, not a stock heading).
    - The ``<p>`` also contains at least one ``<a>`` tag (the first stock).

    Args:
        element: A BeautifulSoup Tag to inspect.

    Returns:
        ``(section_label, stock_name, stock_text)`` or ``None``.
    """
    if element.name.lower() != "p":
        return None

    first_tag = _first_tag_child(element)
    if first_tag is None:
        return None

    first_name = first_tag.name.lower()
    if first_name not in ("strong", "b"):
        return None

    # The bold tag must NOT contain a link (otherwise it's a stock heading)
    if first_tag.find("a") is not None:
        return None

    # The <p> must contain at least one <a> tag after the bold label
    a_tag = element.find("a")
    if a_tag is None:
        return None

    # Extract section label from the bold tag
    section_label = _get_text(first_tag).strip()
    if not section_label:
        return None

    # Extract stock name from the first <a> tag
    stock_name = _get_text(a_tag).strip()
    # Strip trailing colon that MoneyControl sometimes appends (e.g. "Biocon Limited:")
    stock_name = stock_name.rstrip(":").strip()
    if not stock_name:
        return None

    # Extract the remaining text after the <a> tag as the stock's news text.
    # Walk siblings of the <a> tag within the <p> to collect trailing text nodes.
    text_parts: list[str] = []
    collecting = False
    for child in element.children:
        if child is a_tag:
            collecting = True
            continue
        if collecting:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                part = str(child).strip()
                if part:
                    text_parts.append(part)
            elif isinstance(child, Tag):
                # Collect text from any inline tags after the <a> (e.g. <strong></strong>)
                part = _get_text(child).strip()
                if part:
                    text_parts.append(part)

    stock_text = " ".join(text_parts).strip()
    # Strip leading colon/dash that sometimes precedes the news text
    stock_text = stock_text.lstrip(":").strip()

    return section_label, stock_name, stock_text


# ---------------------------------------------------------------------------
# Date normalisation helper
# ---------------------------------------------------------------------------

# Matches ISO 8601 datetime strings like "2024-05-13T06:30:00+05:30" or
# plain date strings like "2024-05-13" or slash-separated "2024/05/13".
_DATE_RE = re.compile(r"(\d{4})[/\-](\d{2})[/\-](\d{2})")


def _normalise_date(raw: str) -> str | None:
    """Extract and normalise a date string to ``YYYY-MM-DD``.

    Accepts ISO 8601 datetime strings (with or without time/timezone),
    slash-separated dates, and plain ``YYYY-MM-DD`` strings.

    Returns ``None`` if no date pattern is found in *raw*.
    """
    match = _DATE_RE.search(raw)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _iter_block_elements(container: Tag) -> Iterator[Tag]:
    """Yield direct and shallow block-level children of *container*.

    We walk the container recursively but yield each Tag only once at the
    shallowest level where it appears as a meaningful block element.  This
    prevents double-counting text that lives inside nested structures.

    Strategy: yield every Tag child of the container; for each yielded Tag
    that is itself a block container (div, article, section, …) recurse into
    it so we can find headings and paragraphs nested inside wrappers.
    """
    _BLOCK_WRAPPERS = {"div", "article", "section", "main", "aside", "header", "footer"}
    _LEAF_BLOCKS = _HEADING_TAGS | {"p", "ul", "ol", "li", "blockquote", "pre"}

    for child in container.children:
        if not isinstance(child, Tag):
            continue
        tag_name = child.name.lower() if child.name else ""
        if tag_name in _LEAF_BLOCKS:
            yield child
        elif tag_name in _BLOCK_WRAPPERS:
            # Recurse into wrapper divs so we find headings/paragraphs inside.
            yield from _iter_block_elements(child)
        else:
            # For other tags (span, a, strong, etc.) yield them so callers can
            # inspect, but don't recurse further.
            yield child


def _is_section_heading(element: Tag) -> bool:
    """Return True if *element* is a section-level heading.

    A section heading is:
    - An ``h1``–``h6`` tag that does NOT contain a hyperlink (those are stock
      headings), OR
    - A ``<p>`` or ``<div>`` whose sole non-whitespace child is a ``<strong>``
      or ``<b>`` tag that does NOT itself contain an ``<a>`` tag.

    The exclusion of hyperlinks ensures that MoneyControl's stock entry pattern
    ``<p><strong><a href="...">Stock Name</a></strong></p>`` is treated as a
    stock heading rather than a section heading.
    """
    tag_name = element.name.lower() if element.name else ""

    if tag_name in _HEADING_TAGS:
        # A heading tag containing a link is a stock heading, not a section.
        if element.find("a") is not None:
            return False
        return True

    if tag_name in ("p", "div"):
        meaningful = _meaningful_children(element)
        if len(meaningful) == 1 and meaningful[0].name.lower() in ("strong", "b"):
            bold_tag = meaningful[0]
            # If the bold tag contains a link, this is a stock heading.
            if bold_tag.find("a") is not None:
                return False
            return True

    return False


def _is_stock_heading(element: Tag) -> bool:
    """Return True if *element* looks like a stock sub-heading.

    A stock heading is a ``<p>`` or heading tag whose first Tag child is an
    ``<a>`` tag (hyperlink), or a ``<p>``/heading whose first Tag child is a
    ``<strong>``/``<b>`` containing an ``<a>``.

    Mixed content (e.g. ``<p><a>Stock</a> — extra text</p>``) is supported:
    we look at the first Tag child regardless of trailing text nodes.

    We deliberately exclude elements that qualify as *section* headings so
    that bold-block section headings are not also treated as stock headings.
    """
    tag_name = element.name.lower() if element.name else ""

    if tag_name in _HEADING_TAGS | {"p"}:
        first_tag = _first_tag_child(element)
        if first_tag is None:
            return False

        first_name = first_tag.name.lower()

        # Direct <a> child → stock heading.
        if first_name == "a":
            return True

        # <strong>/<b> wrapping an <a> → stock heading.
        if first_name in ("strong", "b"):
            inner = _first_tag_child(first_tag)
            if inner is not None and inner.name.lower() == "a":
                return True

    return False


def _get_stock_name(element: Tag) -> str:
    """Extract the stock name from a stock heading element.

    Prefers the text of the first ``<a>`` tag found (Req 3.1).
    Falls back to the full element text.
    Strips trailing colons that MoneyControl sometimes appends (e.g. "Biocon:").
    """
    a_tag = element.find("a")
    if a_tag is not None:
        text = _get_text(a_tag).strip().rstrip(":").strip()
        if text:
            return text

    return _get_text(element).strip().rstrip(":").strip()


def _get_inline_text_after_link(element: Tag) -> str:
    """Return any text that follows the first ``<a>`` tag within *element*.

    Used to capture inline news text in patterns like:
        ``<p><a href="...">Stock Name:</a> News text here.</p>``

    Returns an empty string if there is no trailing text.
    """
    a_tag = element.find("a")
    if a_tag is None:
        return ""

    parts: list[str] = []
    collecting = False
    for child in element.children:
        if child is a_tag:
            collecting = True
            continue
        if collecting:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                part = str(child).strip().lstrip(":").strip()
                if part:
                    parts.append(part)
            elif isinstance(child, Tag):
                part = _get_text(child).strip().lstrip(":").strip()
                if part:
                    parts.append(part)

    return " ".join(parts).strip()


def _meaningful_children(element: Tag) -> list[Tag]:
    """Return the Tag children of *element*, ignoring whitespace strings and comments."""
    result = []
    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            if child.strip():
                # Non-whitespace text node — the element has mixed content,
                # so it's not a "sole child" bold block.
                return []  # signal: mixed content
            continue
        if isinstance(child, Tag):
            result.append(child)
    return result


def _first_tag_child(element: Tag) -> Tag | None:
    """Return the first Tag child of *element*, ignoring whitespace and comments."""
    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            continue
        if isinstance(child, Tag):
            return child
    return None


def _get_text(element: Tag) -> str:
    """Return the visible text of *element*, stripping HTML comments."""
    parts = []
    for item in element.descendants:
        if isinstance(item, Comment):
            continue
        if isinstance(item, NavigableString):
            parts.append(str(item))
    return "".join(parts)
