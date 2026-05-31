"""Unit tests for ArticleParser._find_content_container and _extract_sections."""

import logging

import pytest
from bs4 import BeautifulSoup

from moneycontrol_scraper.parser import ArticleParser

URL = "https://www.moneycontrol.com/news/business/markets/stocks-to-watch-test.html"


def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------------------
# _find_content_container
# ---------------------------------------------------------------------------


class TestFindContentContainer:
    def setup_method(self):
        self.parser = ArticleParser()

    def test_finds_article_desc(self):
        soup = make_soup('<html><body><div class="article-desc"><p>hi</p></div></body></html>')
        container = self.parser._find_content_container(soup)
        assert container.name == "div"
        assert "article-desc" in container.get("class", [])

    def test_finds_content_wrapper(self):
        soup = make_soup('<html><body><div class="content_wrapper"><p>hi</p></div></body></html>')
        container = self.parser._find_content_container(soup)
        assert "content_wrapper" in container.get("class", [])

    def test_finds_article_main_by_id(self):
        soup = make_soup('<html><body><div id="article-main"><p>hi</p></div></body></html>')
        container = self.parser._find_content_container(soup)
        assert container.get("id") == "article-main"

    def test_finds_arti_flow(self):
        soup = make_soup('<html><body><div class="arti-flow"><p>hi</p></div></body></html>')
        container = self.parser._find_content_container(soup)
        assert "arti-flow" in container.get("class", [])

    def test_prefers_first_selector_over_later(self):
        # article-desc should win over content_wrapper
        soup = make_soup(
            '<html><body>'
            '<div class="content_wrapper"><p>second</p></div>'
            '<div class="article-desc"><p>first</p></div>'
            '</body></html>'
        )
        container = self.parser._find_content_container(soup)
        assert "article-desc" in container.get("class", [])

    def test_falls_back_to_body(self):
        soup = make_soup("<html><body><p>no known selector</p></body></html>")
        container = self.parser._find_content_container(soup)
        assert container.name == "body"

    def test_falls_back_to_soup_when_no_body(self):
        soup = make_soup("<p>no body tag</p>")
        container = self.parser._find_content_container(soup)
        # Should return the soup root (BeautifulSoup object acts as Tag)
        assert container is not None


# ---------------------------------------------------------------------------
# _extract_sections — section headings
# ---------------------------------------------------------------------------


class TestExtractSectionsHeadings:
    def setup_method(self):
        self.parser = ArticleParser()

    def _container(self, html: str) -> "Tag":
        soup = make_soup(f"<div>{html}</div>")
        return soup.find("div")

    def test_h2_becomes_section_key(self):
        container = self._container("<h2>Stocks to Watch</h2>")
        sections = self.parser._extract_sections(container, URL)
        assert "Stocks to Watch" in sections

    def test_all_heading_levels_recognised(self):
        html = "".join(f"<h{n}>Section {n}</h{n}>" for n in range(1, 7))
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        for n in range(1, 7):
            assert f"Section {n}" in sections

    def test_heading_text_is_trimmed(self):
        container = self._container("<h2>  Bulk Deals  </h2>")
        sections = self.parser._extract_sections(container, URL)
        assert "Bulk Deals" in sections
        assert "  Bulk Deals  " not in sections

    def test_empty_heading_is_skipped(self):
        container = self._container("<h2></h2><h3>Real Section</h3>")
        sections = self.parser._extract_sections(container, URL)
        assert "" not in sections
        assert "Real Section" in sections

    def test_whitespace_only_heading_is_skipped(self):
        container = self._container("<h2>   </h2><h3>Valid</h3>")
        sections = self.parser._extract_sections(container, URL)
        assert "   " not in sections
        assert "Valid" in sections

    def test_bold_block_p_strong_is_section_heading(self):
        container = self._container("<p><strong>Bulk Deals</strong></p>")
        sections = self.parser._extract_sections(container, URL)
        assert "Bulk Deals" in sections

    def test_bold_block_p_b_is_section_heading(self):
        container = self._container("<p><b>Block Deals</b></p>")
        sections = self.parser._extract_sections(container, URL)
        assert "Block Deals" in sections

    def test_no_headings_returns_empty_and_warns(self, caplog):
        container = self._container("<p>Just a paragraph, no headings.</p>")
        with caplog.at_level(logging.WARNING):
            sections = self.parser._extract_sections(container, URL)
        assert sections == {}
        assert URL in caplog.text

    def test_any_heading_text_accepted_without_filtering(self):
        container = self._container("<h2>Arbitrary Section Name XYZ</h2>")
        sections = self.parser._extract_sections(container, URL)
        assert "Arbitrary Section Name XYZ" in sections


# ---------------------------------------------------------------------------
# _extract_sections — stock entries
# ---------------------------------------------------------------------------


class TestExtractSectionsStockEntries:
    def setup_method(self):
        self.parser = ArticleParser()

    def _container(self, html: str) -> "Tag":
        soup = make_soup(f"<div>{html}</div>")
        return soup.find("div")

    def test_hyperlink_in_p_is_stock_key(self):
        html = (
            "<h2>Stocks to Watch</h2>"
            '<p><a href="/reliance">Reliance Industries</a></p>'
            "<p>Strong Q4 results.</p>"
        )
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        assert "Reliance Industries" in sections["Stocks to Watch"]

    def test_hyperlink_text_preferred_over_surrounding_text(self):
        html = (
            "<h2>Stocks to Watch</h2>"
            '<p><a href="/infosys">Infosys</a> — IT giant</p>'
            "<p>Raised guidance.</p>"
        )
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        # The stock key should be the link text, not the full paragraph text
        assert "Infosys" in sections["Stocks to Watch"]

    def test_paragraphs_concatenated_with_single_space(self):
        html = (
            "<h2>Stocks to Watch</h2>"
            '<p><a href="/tcs">TCS</a></p>'
            "<p>First paragraph.</p>"
            "<p>Second paragraph.</p>"
        )
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        value = sections["Stocks to Watch"]["TCS"]
        assert value == "First paragraph. Second paragraph."

    def test_stock_with_no_paragraphs_has_empty_string_value(self):
        html = (
            "<h2>Stocks to Watch</h2>"
            '<p><a href="/wipro">Wipro</a></p>'
            '<p><a href="/hcl">HCL Tech</a></p>'
            "<p>HCL news.</p>"
        )
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        assert sections["Stocks to Watch"]["Wipro"] == ""
        assert sections["Stocks to Watch"]["HCL Tech"] == "HCL news."

    def test_duplicate_stock_names_accumulate_text(self):
        html = (
            "<h2>Stocks to Watch</h2>"
            '<p><a href="/rel">Reliance</a></p>'
            "<p>First mention.</p>"
            '<p><a href="/rel">Reliance</a></p>'
            "<p>Second mention.</p>"
        )
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        value = sections["Stocks to Watch"]["Reliance"]
        assert "First mention." in value
        assert "Second mention." in value

    def test_section_with_no_stock_entries_is_empty_dict(self):
        html = (
            "<h2>Stocks to Watch</h2>"
            "<p>Some generic text without a stock link.</p>"
            "<h2>Bulk Deals</h2>"
        )
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        # "Stocks to Watch" has no stock sub-headings → {}
        assert sections.get("Stocks to Watch") == {}

    def test_multiple_sections_parsed_independently(self):
        html = (
            "<h2>Stocks to Watch</h2>"
            '<p><a href="/a">Alpha</a></p>'
            "<p>Alpha news.</p>"
            "<h2>Bulk Deals</h2>"
            '<p><a href="/b">Beta</a></p>'
            "<p>Beta news.</p>"
        )
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        assert "Alpha" in sections["Stocks to Watch"]
        assert "Beta" in sections["Bulk Deals"]
        assert "Alpha" not in sections["Bulk Deals"]

    def test_stock_key_and_value_are_stripped(self):
        html = (
            "<h2>Stocks to Watch</h2>"
            '<p><a href="/x">  Trimmed Stock  </a></p>'
            "<p>  Some news.  </p>"
        )
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        assert "Trimmed Stock" in sections["Stocks to Watch"]
        assert sections["Stocks to Watch"]["Trimmed Stock"] == "Some news."

    def test_section_with_only_comments_is_empty_dict(self):
        html = (
            "<h2>Stocks to Watch</h2>"
            "<!-- this is a comment -->"
        )
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        assert sections.get("Stocks to Watch") == {}

    def test_h3_stock_heading_with_link(self):
        html = (
            "<h2>Stocks to Watch</h2>"
            '<h3><a href="/hdfc">HDFC Bank</a></h3>'
            "<p>FII sold shares.</p>"
        )
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        assert "HDFC Bank" in sections["Stocks to Watch"]
        assert sections["Stocks to Watch"]["HDFC Bank"] == "FII sold shares."

    def test_nested_content_in_div_wrapper(self):
        """Headings and paragraphs inside a wrapper div are still found."""
        html = (
            '<div class="wrapper">'
            "<h2>Stocks to Watch</h2>"
            '<p><a href="/sbi">SBI</a></p>'
            "<p>Bank news.</p>"
            "</div>"
        )
        container = self._container(html)
        sections = self.parser._extract_sections(container, URL)
        assert "SBI" in sections["Stocks to Watch"]


# ---------------------------------------------------------------------------
# _extract_date — unit tests (Task 3.6 / Requirements 4.1, 4.2, 4.3)
# ---------------------------------------------------------------------------


class TestExtractDate:
    def setup_method(self):
        self.parser = ArticleParser()

    def _soup(self, html: str) -> BeautifulSoup:
        return make_soup(html)

    # --- Priority 1: JSON-LD datePublished ---

    def test_json_ld_date_published_plain(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type": "NewsArticle", "datePublished": "2024-05-13"}'
            "</script>"
        )
        result = self.parser._extract_date(self._soup(html), URL)
        assert result == "2024-05-13"

    def test_json_ld_date_published_with_time(self):
        html = (
            '<script type="application/ld+json">'
            '{"datePublished": "2024-05-13T06:30:00+05:30"}'
            "</script>"
        )
        result = self.parser._extract_date(self._soup(html), URL)
        assert result == "2024-05-13"

    def test_json_ld_array_of_objects(self):
        html = (
            '<script type="application/ld+json">'
            '[{"@type": "BreadcrumbList"}, {"datePublished": "2024-06-01"}]'
            "</script>"
        )
        result = self.parser._extract_date(self._soup(html), URL)
        assert result == "2024-06-01"

    def test_json_ld_invalid_json_is_skipped(self):
        # Invalid JSON-LD should be skipped; fall through to URL fallback
        url_with_date = "https://www.moneycontrol.com/news/2024/07/15/article.html"
        html = '<script type="application/ld+json">NOT JSON</script>'
        result = self.parser._extract_date(self._soup(html), url_with_date)
        assert result == "2024-07-15"

    # --- Priority 2: article:published_time meta ---

    def test_meta_article_published_time(self):
        html = '<meta property="article:published_time" content="2024-05-14T08:00:00Z">'
        result = self.parser._extract_date(self._soup(html), URL)
        assert result == "2024-05-14"

    def test_meta_og_article_published_time(self):
        html = '<meta property="og:article:published_time" content="2024-05-15">'
        result = self.parser._extract_date(self._soup(html), URL)
        assert result == "2024-05-15"

    # --- Priority 3: meta name="publish-date" and <time datetime> ---

    def test_meta_publish_date(self):
        html = '<meta name="publish-date" content="2024-05-16">'
        result = self.parser._extract_date(self._soup(html), URL)
        assert result == "2024-05-16"

    def test_time_datetime_element(self):
        html = '<time datetime="2024-05-17T10:00:00">May 17, 2024</time>'
        result = self.parser._extract_date(self._soup(html), URL)
        assert result == "2024-05-17"

    # --- Priority 4: URL path regex ---

    def test_date_from_url_slash_format(self):
        url = "https://www.moneycontrol.com/news/2024/05/18/stocks-to-watch.html"
        result = self.parser._extract_date(self._soup("<html></html>"), url)
        assert result == "2024-05-18"

    def test_date_from_url_hyphen_format(self):
        url = "https://www.moneycontrol.com/news/stocks-to-watch-2024-05-19.html"
        result = self.parser._extract_date(self._soup("<html></html>"), url)
        assert result == "2024-05-19"

    # --- Priority 5: no date found → None + warning ---

    def test_no_date_returns_none_and_warns(self, caplog):
        url_no_date = "https://www.moneycontrol.com/news/stocks-to-watch.html"
        with caplog.at_level(logging.WARNING):
            result = self.parser._extract_date(self._soup("<html></html>"), url_no_date)
        assert result is None
        assert url_no_date in caplog.text

    # --- Priority ordering ---

    def test_json_ld_takes_priority_over_meta(self):
        html = (
            '<script type="application/ld+json">'
            '{"datePublished": "2024-01-01"}'
            "</script>"
            '<meta property="article:published_time" content="2024-12-31">'
        )
        result = self.parser._extract_date(self._soup(html), URL)
        assert result == "2024-01-01"

    def test_meta_takes_priority_over_url(self):
        url = "https://www.moneycontrol.com/news/2024/12/31/article.html"
        html = '<meta property="article:published_time" content="2024-01-01">'
        result = self.parser._extract_date(self._soup(html), url)
        assert result == "2024-01-01"


# ---------------------------------------------------------------------------
# Property-based tests (Tasks 3.2 – 3.5)
# ---------------------------------------------------------------------------

import html as html_module

from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Task 3.2 — Property 2: Section headings are extracted and trimmed
# Feature: moneycontrol-stocks-scraper, Property 2: Section headings are extracted and trimmed
# Validates: Requirements 2.1, 2.2, 2.4
# ---------------------------------------------------------------------------


class TestProperty2SectionHeadings:
    """Property 2: Section headings are extracted and trimmed."""

    def setup_method(self):
        self.parser = ArticleParser()

    def _container(self, html: str):
        soup = make_soup(f"<div>{html}</div>")
        return soup.find("div")

    @settings(max_examples=100)
    @given(
        headings=st.lists(
            st.text(min_size=1).filter(lambda t: t.strip()),
            min_size=1,
            max_size=10,
        )
    )
    def test_non_empty_headings_appear_as_trimmed_keys(self, headings):
        # Feature: moneycontrol-stocks-scraper, Property 2: Section headings are extracted and trimmed
        # Escape text before embedding in HTML; BS4 will unescape back to original.
        inner_html = "".join(f"<h2>{html_module.escape(text)}</h2>" for text in headings)
        container = self._container(inner_html)
        sections = self.parser._extract_sections(container, URL)

        for text in headings:
            trimmed = text.strip()
            if trimmed:
                assert trimmed in sections, (
                    f"Expected trimmed heading {trimmed!r} to be a key in sections"
                )

    @settings(max_examples=100)
    @given(
        headings=st.lists(
            st.text(min_size=1).filter(lambda t: t.strip()),
            min_size=1,
            max_size=10,
        )
    )
    def test_no_whitespace_only_key_in_sections(self, headings):
        # Feature: moneycontrol-stocks-scraper, Property 2: Section headings are extracted and trimmed
        inner_html = "".join(f"<h2>{html_module.escape(text)}</h2>" for text in headings)
        container = self._container(inner_html)
        sections = self.parser._extract_sections(container, URL)

        for key in sections:
            assert key.strip() != "", (
                f"Whitespace-only key {key!r} should not appear in sections"
            )
            assert key == key.strip(), (
                f"Key {key!r} should be trimmed (no leading/trailing whitespace)"
            )


# ---------------------------------------------------------------------------
# Task 3.3 — Property 3: Stock name keys prefer hyperlink text
# Feature: moneycontrol-stocks-scraper, Property 3: Stock name keys prefer hyperlink text
# Validates: Requirement 3.1
# ---------------------------------------------------------------------------


class TestProperty3StockNamePrefersLink:
    """Property 3: Stock name keys prefer hyperlink text."""

    def setup_method(self):
        self.parser = ArticleParser()

    def _container(self, html: str):
        soup = make_soup(f"<div>{html}</div>")
        return soup.find("div")

    @settings(max_examples=100)
    @given(
        link_text=st.text(min_size=1).filter(lambda t: t.strip()),
        outer_text=st.text(min_size=1).filter(lambda t: t.strip()),
    )
    def test_stock_key_equals_link_text_not_full_paragraph(self, link_text, outer_text):
        # Feature: moneycontrol-stocks-scraper, Property 3: Stock name keys prefer hyperlink text
        # Escape text before embedding in HTML; BS4 will unescape back to original.
        inner_html = (
            "<h2>Section</h2>"
            f'<p><a href="/x">{html_module.escape(link_text)}</a> {html_module.escape(outer_text)}</p>'
            "<p>News text.</p>"
        )
        container = self._container(inner_html)
        sections = self.parser._extract_sections(container, URL)

        expected_key = link_text.strip()
        assert "Section" in sections, "Section heading should be present"
        stock_keys = list(sections["Section"].keys())
        assert expected_key in stock_keys, (
            f"Expected stock key {expected_key!r} (link text) in {stock_keys!r}"
        )
        # The full paragraph text (link + outer) should NOT be the key
        full_para = f"{link_text} {outer_text}".strip()
        if full_para != expected_key:
            assert full_para not in stock_keys, (
                f"Full paragraph text {full_para!r} should not be the stock key"
            )


# ---------------------------------------------------------------------------
# Task 3.4 — Property 4: Paragraph concatenation with single space
# Feature: moneycontrol-stocks-scraper, Property 4: Paragraph concatenation with single space
# Validates: Requirements 3.2, 3.4
# ---------------------------------------------------------------------------


class TestProperty4ParagraphConcatenation:
    """Property 4: Paragraph concatenation with single space."""

    def setup_method(self):
        self.parser = ArticleParser()

    def _container(self, html: str):
        soup = make_soup(f"<div>{html}</div>")
        return soup.find("div")

    @settings(max_examples=100)
    @given(
        paras=st.lists(st.text(min_size=1), min_size=2, max_size=8)
    )
    def test_paragraphs_joined_with_single_space(self, paras):
        # Feature: moneycontrol-stocks-scraper, Property 4: Paragraph concatenation with single space
        # Escape text before embedding in HTML; BS4 will unescape back to original.
        para_html = "".join(f"<p>{html_module.escape(p)}</p>" for p in paras)
        inner_html = (
            "<h2>Section</h2>"
            '<p><a href="/x">Stock</a></p>'
            + para_html
        )
        container = self._container(inner_html)
        sections = self.parser._extract_sections(container, URL)

        expected = " ".join(p.strip() for p in paras if p.strip())
        assert "Section" in sections
        assert "Stock" in sections["Section"], (
            f"Stock key missing; sections: {sections}"
        )
        actual = sections["Section"]["Stock"]
        assert actual == expected, (
            f"Expected {expected!r}, got {actual!r}"
        )


# ---------------------------------------------------------------------------
# Task 3.5 — Property 5: Duplicate stock names accumulate text
# Feature: moneycontrol-stocks-scraper, Property 5: Duplicate stock names accumulate text
# Validates: Requirement 3.3
# ---------------------------------------------------------------------------


class TestProperty5DuplicateStockAccumulation:
    """Property 5: Duplicate stock names accumulate text."""

    def setup_method(self):
        self.parser = ArticleParser()

    def _container(self, html: str):
        soup = make_soup(f"<div>{html}</div>")
        return soup.find("div")

    @settings(max_examples=100)
    @given(
        stock_name=st.text(min_size=1).filter(lambda t: t.strip()),
        paras_first=st.lists(st.text(min_size=1).filter(lambda t: t.strip()), min_size=1, max_size=4),
        paras_second=st.lists(st.text(min_size=1).filter(lambda t: t.strip()), min_size=1, max_size=4),
    )
    def test_duplicate_stock_produces_single_entry_with_all_text(
        self, stock_name, paras_first, paras_second
    ):
        # Feature: moneycontrol-stocks-scraper, Property 5: Duplicate stock names accumulate text
        # HTML-escape all generated text so special chars don't corrupt the HTML structure.
        first_paras_html = "".join(f"<p>{html_module.escape(p)}</p>" for p in paras_first)
        second_paras_html = "".join(f"<p>{html_module.escape(p)}</p>" for p in paras_second)
        escaped_name = html_module.escape(stock_name)
        inner_html = (
            "<h2>Section</h2>"
            f'<p><a href="/x">{escaped_name}</a></p>'
            + first_paras_html
            + f'<p><a href="/x">{escaped_name}</a></p>'
            + second_paras_html
        )
        container = self._container(inner_html)
        sections = self.parser._extract_sections(container, URL)

        key = stock_name.strip()
        assert "Section" in sections, "Section heading should be present"
        assert key in sections["Section"], (
            f"Stock key {key!r} should be present; got {list(sections['Section'].keys())!r}"
        )

        # There should be exactly one entry for this stock name
        assert list(sections["Section"].keys()).count(key) == 1, (
            "Duplicate stock name should produce a single key"
        )

        combined_value = sections["Section"][key]

        # All paragraphs from both appearances should be present in the value
        for p in paras_first:
            assert p.strip() in combined_value, (
                f"Text from first appearance {p.strip()!r} missing in {combined_value!r}"
            )
        for p in paras_second:
            assert p.strip() in combined_value, (
                f"Text from second appearance {p.strip()!r} missing in {combined_value!r}"
            )


# ---------------------------------------------------------------------------
# Task 3.7 — Property 6: Date extraction from metadata produces ISO 8601 format
# Feature: moneycontrol-stocks-scraper, Property 6: Date extraction from metadata produces ISO 8601 format
# Validates: Requirement 4.1
# ---------------------------------------------------------------------------

import re
from datetime import date

from hypothesis import given, settings
from hypothesis import strategies as st


class TestDateFromMetadataProperty:
    """Property 6: Date extraction from metadata produces ISO 8601 format.

    **Validates: Requirements 4.1**
    """

    # Feature: moneycontrol-stocks-scraper, Property 6: Date extraction from metadata produces ISO 8601 format

    def setup_method(self):
        self.parser = ArticleParser()
        self._iso_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    @given(st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31)))
    @settings(max_examples=100)
    def test_json_ld_date_published_iso8601(self, d: date):
        """JSON-LD datePublished returns YYYY-MM-DD."""
        date_str = d.strftime("%Y-%m-%d")
        html = (
            f'<script type="application/ld+json">'
            f'{{"datePublished": "{date_str}"}}'
            f"</script>"
        )
        result = self.parser._extract_date(make_soup(html), URL)
        assert result is not None, f"Expected a date for {date_str}, got None"
        assert self._iso_pattern.match(result), (
            f"Expected ISO 8601 format, got {result!r}"
        )
        assert result == date_str

    @given(st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31)))
    @settings(max_examples=100)
    def test_meta_article_published_time_iso8601(self, d: date):
        """article:published_time meta tag returns YYYY-MM-DD."""
        date_str = d.strftime("%Y-%m-%d")
        html = (
            f'<meta property="article:published_time" '
            f'content="{date_str}T00:00:00Z">'
        )
        result = self.parser._extract_date(make_soup(html), URL)
        assert result is not None, f"Expected a date for {date_str}, got None"
        assert self._iso_pattern.match(result), (
            f"Expected ISO 8601 format, got {result!r}"
        )
        assert result == date_str

    @given(st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31)))
    @settings(max_examples=100)
    def test_time_element_datetime_iso8601(self, d: date):
        """<time datetime> element returns YYYY-MM-DD."""
        date_str = d.strftime("%Y-%m-%d")
        html = f'<time datetime="{date_str}">some text</time>'
        result = self.parser._extract_date(make_soup(html), URL)
        assert result is not None, f"Expected a date for {date_str}, got None"
        assert self._iso_pattern.match(result), (
            f"Expected ISO 8601 format, got {result!r}"
        )
        assert result == date_str


# ---------------------------------------------------------------------------
# Task 3.8 — Property 7: Date fallback extracts from URL path
# Feature: moneycontrol-stocks-scraper, Property 7: Date fallback extracts from URL path
# Validates: Requirement 4.2
# ---------------------------------------------------------------------------


class TestDateFromURLProperty:
    """Property 7: Date fallback extracts from URL path.

    **Validates: Requirements 4.2**
    """

    # Feature: moneycontrol-stocks-scraper, Property 7: Date fallback extracts from URL path

    def setup_method(self):
        self.parser = ArticleParser()
        self._empty_html = "<html></html>"

    @given(st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31)))
    @settings(max_examples=100)
    def test_slash_format_url_date_extraction(self, d: date):
        """URL with YYYY/MM/DD slash format returns correct YYYY-MM-DD date."""
        year = d.strftime("%Y")
        month = d.strftime("%m")
        day = d.strftime("%d")
        expected = f"{year}-{month}-{day}"
        url = f"https://www.moneycontrol.com/news/{year}/{month}/{day}/article.html"
        result = self.parser._extract_date(make_soup(self._empty_html), url)
        assert result == expected, (
            f"Expected {expected!r} from slash URL, got {result!r}"
        )

    @given(st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31)))
    @settings(max_examples=100)
    def test_hyphen_format_url_date_extraction(self, d: date):
        """URL with YYYY-MM-DD hyphen format returns correct YYYY-MM-DD date."""
        year = d.strftime("%Y")
        month = d.strftime("%m")
        day = d.strftime("%d")
        expected = f"{year}-{month}-{day}"
        url = (
            f"https://www.moneycontrol.com/news/"
            f"article-{year}-{month}-{day}.html"
        )
        result = self.parser._extract_date(make_soup(self._empty_html), url)
        assert result == expected, (
            f"Expected {expected!r} from hyphen URL, got {result!r}"
        )


# ---------------------------------------------------------------------------
# Task 3.10 — Unit tests for ArticleParser edge cases
# ---------------------------------------------------------------------------


class TestArticleParserEdgeCases:
    """Edge case unit tests for ArticleParser.

    Covers Requirements 2.3, 3.5, 3.6, and 4.3.
    """

    def setup_method(self):
        self.parser = ArticleParser()

    def _container(self, html: str) -> "Tag":
        soup = make_soup(f"<div>{html}</div>")
        return soup.find("div")

    # --- Requirement 2.3: HTML with no headings → sections == {} + warning ---

    def test_no_headings_returns_empty_sections(self, caplog):
        """HTML with no heading elements returns empty sections dict (Req 2.3)."""
        container = self._container(
            "<p>This is just a paragraph with no headings at all.</p>"
            "<p>Another paragraph here.</p>"
        )
        with caplog.at_level(logging.WARNING):
            sections = self.parser._extract_sections(container, URL)
        assert sections == {}

    def test_no_headings_logs_warning_with_url(self, caplog):
        """Warning logged with URL when no headings found (Req 2.3)."""
        container = self._container("<p>No headings here.</p>")
        with caplog.at_level(logging.WARNING):
            self.parser._extract_sections(container, URL)
        assert URL in caplog.text

    # --- Requirement 3.5: Section heading with no stock entries → {} ---

    def test_section_with_no_stock_entries_maps_to_empty_dict(self):
        """A section heading followed by no stock links maps to {} (Req 3.5)."""
        container = self._container(
            "<h2>Empty Section</h2>"
            "<p>Some generic text without any stock hyperlinks.</p>"
        )
        sections = self.parser._extract_sections(container, URL)
        assert "Empty Section" in sections
        assert sections["Empty Section"] == {}

    def test_section_with_only_plain_text_maps_to_empty_dict(self):
        """Section with only plain paragraphs (no links) maps to {} (Req 3.5)."""
        container = self._container(
            "<h2>Bulk Deals</h2>"
            "<p>No stocks mentioned here.</p>"
            "<p>Just commentary.</p>"
        )
        sections = self.parser._extract_sections(container, URL)
        assert sections.get("Bulk Deals") == {}

    # --- Requirement 3.6: Stock heading with no paragraphs → "" ---

    def test_stock_heading_with_no_paragraphs_maps_to_empty_string(self):
        """Stock heading immediately followed by next heading maps to '' (Req 3.6)."""
        container = self._container(
            "<h2>Stocks to Watch</h2>"
            '<p><a href="/wipro">Wipro</a></p>'
            '<p><a href="/tcs">TCS</a></p>'
            "<p>TCS had strong results.</p>"
        )
        sections = self.parser._extract_sections(container, URL)
        assert sections["Stocks to Watch"]["Wipro"] == ""
        assert sections["Stocks to Watch"]["TCS"] == "TCS had strong results."

    def test_last_stock_heading_with_no_paragraphs_maps_to_empty_string(self):
        """Last stock heading in section with no paragraphs maps to '' (Req 3.6)."""
        container = self._container(
            "<h2>Stocks to Watch</h2>"
            '<p><a href="/infosys">Infosys</a></p>'
            "<p>Infosys raised guidance.</p>"
            '<p><a href="/wipro">Wipro</a></p>'
        )
        sections = self.parser._extract_sections(container, URL)
        assert sections["Stocks to Watch"]["Wipro"] == ""
        assert sections["Stocks to Watch"]["Infosys"] == "Infosys raised guidance."

    # --- Requirement 4.3: No date metadata and no date in URL → None + warning ---

    def test_no_date_metadata_and_no_date_in_url_returns_none(self, caplog):
        """No date in metadata or URL returns None (Req 4.3)."""
        url_no_date = "https://www.moneycontrol.com/news/stocks-to-watch.html"
        soup = make_soup("<html><body><p>No date here.</p></body></html>")
        with caplog.at_level(logging.WARNING):
            result = self.parser._extract_date(soup, url_no_date)
        assert result is None

    def test_no_date_logs_warning_with_url(self, caplog):
        """Warning logged with URL when no date found (Req 4.3)."""
        url_no_date = "https://www.moneycontrol.com/news/stocks-to-watch.html"
        soup = make_soup("<html><body><p>No date here.</p></body></html>")
        with caplog.at_level(logging.WARNING):
            self.parser._extract_date(soup, url_no_date)
        assert url_no_date in caplog.text

    def test_output_record_included_when_date_is_none(self):
        """OutputRecord is still produced (not rejected) when date is None (Req 4.3)."""
        url_no_date = "https://www.moneycontrol.com/news/stocks-to-watch.html"
        html = "<html><body><h2>Stocks to Watch</h2></body></html>"
        record = self.parser.parse(html, url_no_date)
        # Record should exist with date=None, not raise an exception
        assert record is not None
        assert record.date is None
        assert record.url == url_no_date
