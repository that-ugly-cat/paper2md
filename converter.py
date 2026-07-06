"""PDF -> clean text pipeline, adapted from ReadPDF v2 (Docling low-level API).

Works directly on the DoclingDocument instead of exporting to Markdown and
cleaning it with regexes: uses the layout model for reading order, drops
tables/images/captions/headers/footers/footnotes, splits TextItems on
prov.charspan, strips marginal noise (copyright/download notices), and
rejoins sentences interrupted by marginal elements.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from time import perf_counter
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

EXCLUDED_LABELS = {
    "footnote", "page_header", "page_footer",
    "caption", "picture", "table", "formula",
}

HEADING_LABELS = {"title", "section_header"}

REFERENCE_HEADINGS = {
    "references", "reference", "bibliography", "literature cited",
    "works cited", "références", "bibliographie", "bibliografia",
    "riferimenti bibliografici", "literaturverzeichnis",
}

OPTIONAL_END_HEADINGS = {
    "acknowledgements", "acknowledgments", "author contributions",
    "authors' contributions", "funding", "conflict of interest",
    "conflicts of interest", "competing interests", "data availability",
    "data availability statement", "ethics statement",
    "supplementary material", "supplementary materials",
}

NOISE_PATTERNS = [
    r"^copyright\b", r"^©\s*\d{4}", r"^distributed under\b",
    r"^exclusive licensee\b", r"^no claim to original\b",
    r"^creative commons\b", r"^downloaded from\b",
    r"^downloaded on\b", r"^https?://\S+$",
]

STRONG_SENTENCE_END = re.compile(r"[.!?][\"'’”\)\]]*$")

# A URL running from its scheme to the end of the line. Papers often justify or
# letter-space long URLs, so the layout model reports them with whitespace (and
# tabs) scattered through — either glyph-by-glyph ("h t t p s : / / d o i ...")
# or in chunks ("https:/ /doi.or g/..."). We reconstruct those, and shield every
# URL from the punctuation/en-dash normalization that would otherwise mangle it.
# The scheme itself may be broken glyph-by-glyph ("h t t p s : / /"), so allow
# whitespace between every character of it.
_URL_TO_EOL = re.compile(r"h\s*t\s*t\s*p\s*s?\s*:\s*/\s*/[^\n]*", re.IGNORECASE)
_URL_SOLID = re.compile(r"https?://\S+")


def reflow_spaced_urls(text: str) -> str:
    """Collapse whitespace inside URLs the PDF broke up by justification. Only
    fires when the domain itself contains whitespace — a domain can't legally
    hold a space, so that's a reliable fragmentation signal and leaves normal
    inline URLs ('see https://x for details') untouched."""
    def fix(match: Any) -> str:
        span = match.group(0)
        after_scheme = re.split(r"/\s*/", span, maxsplit=1)[-1]
        domain = re.split(r"/", after_scheme, maxsplit=1)[0]
        if not re.search(r"\S\s+\S", domain):
            return span
        return re.sub(r"\s+", "", span)
    return _URL_TO_EOL.sub(fix, text)


def make_converter() -> DocumentConverter:
    """Converter configured for born-digital scientific papers (no OCR, no tables)."""
    options = PdfPipelineOptions()
    options.do_ocr = False
    options.do_table_structure = False
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def label_name(item: Any) -> str:
    label = getattr(item, "label", None)
    if label is None:
        return ""
    if hasattr(label, "value"):
        return str(label.value).lower()
    return str(label).lower().rsplit(".", 1)[-1]


def get_item_segments(item: Any) -> list[dict[str, Any]]:
    text = getattr(item, "text", "") or ""
    provenance = list(getattr(item, "prov", None) or [])

    if not provenance:
        return ([{"text": text.strip(), "page_no": None, "bbox": None, "charspan": None}]
                if text.strip() else [])

    segments = []
    for prov in provenance:
        charspan = getattr(prov, "charspan", None)
        if not charspan or len(charspan) != 2:
            continue
        start, end = max(0, int(charspan[0])), min(len(text), int(charspan[1]))
        if end <= start:
            continue
        segment_text = text[start:end].strip()
        if segment_text:
            segments.append({
                "text": segment_text,
                "page_no": getattr(prov, "page_no", None),
                "bbox": getattr(prov, "bbox", None),
                "charspan": (start, end),
            })

    if not segments and text.strip():
        segments.append({"text": text.strip(), "page_no": None, "bbox": None, "charspan": None})
    return segments


def normalize_heading(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip(" .:;–—-").lower()


def is_noise_segment(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip()
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in NOISE_PATTERNS)


def document_to_blocks(
    doc: Any,
    remove_references: bool = True,
    remove_end_matter: bool = False,
) -> list[dict[str, Any]]:
    blocks = []
    stop_headings = set()
    if remove_references:
        stop_headings |= REFERENCE_HEADINGS
    if remove_end_matter:
        stop_headings |= OPTIONAL_END_HEADINGS

    for item, level in doc.iterate_items():
        label = label_name(item)
        text = (getattr(item, "text", "") or "").strip()
        if not text or label in EXCLUDED_LABELS:
            continue

        if label in HEADING_LABELS and normalize_heading(text) in stop_headings:
            break

        for segment in get_item_segments(item):
            segment_text = segment["text"].strip()
            if not segment_text or is_noise_segment(segment_text):
                continue
            blocks.append({
                "text": segment_text, "label": label, "level": level,
                "page_no": segment.get("page_no"),
                "bbox": segment.get("bbox"),
                "charspan": segment.get("charspan"),
            })
    return blocks


def should_merge_blocks(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if previous["label"] in HEADING_LABELS or current["label"] in HEADING_LABELS:
        return False
    left, right = previous["text"].strip(), current["text"].strip()
    if not left or not right or STRONG_SENTENCE_END.search(left):
        return False
    return right[0].islower() or right[0] in ",;:)]}%"


def merge_continuation_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = []
    for block in blocks:
        current = block.copy()
        if merged and should_merge_blocks(merged[-1], current):
            merged[-1]["text"] = merged[-1]["text"].rstrip() + " " + current["text"].lstrip()
        else:
            merged.append(current)
    return merged


def normalize_extracted_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "").replace("​", "").replace("﻿", "")
    text = text.replace("\xa0", " ").replace("ﬁ", "fi").replace("ﬂ", "fl")

    # Reconstruct justified/letter-spaced URLs, then park every URL behind a
    # placeholder so the punctuation and digit-hyphen-to-en-dash rules below
    # (meant for prose and page ranges) can't corrupt DOIs and query strings.
    text = reflow_spaced_urls(text)
    urls: list[str] = []
    def _stash(match: Any) -> str:
        urls.append(match.group(0))
        return f"\x00URL{len(urls) - 1}\x00"
    text = _URL_SOLID.sub(_stash, text)

    text = re.sub(r"\b([A-Za-z]+)\s+[’']\s+s\b", r"\1's", text)
    text = re.sub(r"\b([A-Za-z]+)\s+[’'](?=\s)", r"\1'", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,;:!?])(?=[A-Za-zÀ-ÖØ-öø-ÿ])", r"\1 ", text)
    text = re.sub(r"(?<=\d)\s*[-–]\s*(?=\d)", "–", text)
    text = re.sub(r"\bGPT3\b", "GPT-3", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines()).strip()

    for i, url in enumerate(urls):
        text = text.replace(f"\x00URL{i}\x00", url)
    return text


def blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    return "\n\n".join(block["text"].strip() for block in blocks if block["text"].strip())


def pdf_to_clean_text(
    converter: DocumentConverter,
    pdf_path: str | Path,
    remove_references: bool = True,
    remove_end_matter: bool = False,
) -> dict[str, Any]:
    """Run the full pipeline and return text + markdown + basic stats. Blocking/CPU-bound."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    started = perf_counter()
    result = converter.convert(pdf_path)
    conversion_seconds = perf_counter() - started

    raw_markdown = result.document.export_to_markdown(image_placeholder="")

    blocks = document_to_blocks(
        result.document,
        remove_references=remove_references,
        remove_end_matter=remove_end_matter,
    )
    blocks = merge_continuation_blocks(blocks)
    clean_text = normalize_extracted_text(blocks_to_text(blocks))

    pages = {b["page_no"] for b in blocks if b.get("page_no") is not None}

    return {
        "text": clean_text,
        "markdown": raw_markdown,
        "blocks": len(blocks),
        "pages": len(pages),
        "seconds": round(conversion_seconds, 1),
    }
