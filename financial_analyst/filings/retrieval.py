"""Deterministic lexical retrieval over filing paragraphs."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser

from .sec import FilingMetadata, FilingRetrievalError

_TOKEN = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)?", re.IGNORECASE)
_STOP_WORDS = frozenset(
    "a an and are as at be by for from has have in is it of on or that the to was "
    "were what when where which who why with about company explain describe discuss "
    "find give tell show summarize".split()
)
_BLOCK_TAGS = frozenset(
    "address article aside blockquote br dd div dl dt fieldset figcaption figure footer "
    "h1 h2 h3 h4 h5 h6 header hr li main nav ol p section table td th tr ul".split()
)
_IGNORED_TAGS = frozenset({"script", "style", "noscript", "ix:header"})


@dataclass(frozen=True)
class EvidenceChunk:
    ticker: str
    company_name: str
    form: str
    filing_date: str
    report_date: str
    accession_number: str
    source_url: str
    paragraph: int
    score: float
    text: str

    def to_dict(self) -> dict:
        return {**asdict(self), "citation": self.citation}

    @property
    def citation(self) -> str:
        return (
            f"{self.company_name}, Form {self.form}, filed {self.filing_date}, "
            f"accession {self.accession_number}, paragraph {self.paragraph}: {self.source_url}"
        )


class _ParagraphParser(HTMLParser):
    """Extract readable text and preserve paragraph-like boundaries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _IGNORED_TAGS:
            self.ignored_depth += 1
        elif not self.ignored_depth and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORED_TAGS and self.ignored_depth:
            self.ignored_depth -= 1
        elif not self.ignored_depth and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def _paragraphs(html: str) -> list[str]:
    parser = _ParagraphParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as error:  # malformed HTML should not crash the stage
        raise FilingRetrievalError("Could not parse the SEC filing HTML.") from error
    raw = "".join(parser.parts)
    return [
        cleaned
        for part in re.split(r"\n+", raw)
        if (cleaned := re.sub(r"\s+", " ", part).strip())
    ]


def _terms(text: str) -> list[str]:
    return [
        token.lower()
        for token in _TOKEN.findall(text)
        if len(token) > 1 and token.lower() not in _STOP_WORDS
    ]


def _score(paragraph: str, query_terms: set[str], query_phrase: str) -> float:
    tokens = _terms(paragraph)
    if not tokens or not query_terms:
        return 0.0
    token_set = set(tokens)
    matched = query_terms & token_set
    if not matched:
        return 0.0
    # Favor passages covering more distinct query concepts while keeping long
    # table-heavy paragraphs from winning on raw term repetition alone.
    coverage = len(matched) / len(query_terms)
    density = len(matched) / max(len(token_set), 1)
    phrase = query_phrase.lower() in paragraph.lower() if query_phrase else False
    return coverage + min(density, 0.25) + (0.5 if phrase else 0.0)


def search_filing(
    filing: FilingMetadata,
    html: str,
    query: str,
    *,
    top_k: int = 5,
    max_chars: int = 1400,
) -> list[EvidenceChunk]:
    """Rank filing paragraphs for a query and attach source metadata.

    This is lexical search, not semantic interpretation. Empty results mean no
    paragraph matched the query terms; callers should treat that as abstention.
    """
    if not query.strip():
        raise FilingRetrievalError("Query cannot be blank.")
    if top_k < 1:
        raise FilingRetrievalError("top_k must be at least 1.")
    if max_chars < 100:
        raise FilingRetrievalError("max_chars must be at least 100.")

    terms = set(_terms(query))
    phrase = " ".join(query.split())
    ranked: list[tuple[float, int, str]] = []
    for number, paragraph in enumerate(_paragraphs(html), start=1):
        if len(paragraph) > max_chars:
            paragraph = paragraph[:max_chars].rsplit(" ", 1)[0] + " …"
        score = _score(paragraph, terms, phrase)
        if score:
            ranked.append((score, number, paragraph))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [
        EvidenceChunk(
            ticker=filing.ticker,
            company_name=filing.company_name,
            form=filing.form,
            filing_date=filing.filing_date,
            report_date=filing.report_date,
            accession_number=filing.accession_number,
            source_url=filing.url,
            paragraph=number,
            score=round(score, 6),
            text=text,
        )
        for score, number, text in ranked[:top_k]
    ]
