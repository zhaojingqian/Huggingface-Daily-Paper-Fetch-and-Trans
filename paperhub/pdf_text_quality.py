#!/usr/bin/env python3
"""Conservative translation-quality checks for PDFs without retained TeX.

The normal publication gate inspects translated TeX because it can distinguish
paper prose from LaTeX structure precisely.  Historical PDFs do not always
have a TeX backup, so this module provides an explicitly enabled fallback:
extract bounded page text with Poppler, ignore reference/source-data pages, and
only report sustained English-dominant prose.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict


PDF_TEXT_POLICY_VERSION = "pdf-text-quality-2026-07-28-v18"
DEFAULT_MAX_PAGES = 200
DEFAULT_MAX_TEXT_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 120

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LETTER_RE = re.compile(r"[A-Za-z]")
WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z-]{2,}\b")
REFERENCE_HEADING_RE = re.compile(
    r"(?im)(?:^|[ \t]{4,})[ \t]*"
    r"(?:参考文献|references|bibliography)"
    r"(?=$|[ \t]{3,})"
)
APPENDIX_HEADING_RE = re.compile(
    r"(?im)^\s*(?:"
    r"附录(?:\s*[A-Z0-9])?"
    r"|appendix(?:\s+[A-Z0-9])?"
    r"|supplementary\s+(?:material|information)"
    r")\b"
)
BARE_APPENDIX_HEADING_RE = re.compile(
    r"^\s*([A-Z])\s+[A-Z][^\n]{2,100}\s*$"
)
BARE_APPENDIX_SUBHEADING_RE = re.compile(
    r"^\s*([A-Z])\.\d+(?:\.\d+)*\s+\S"
)
SOURCE_DATA_MARKER_RE = re.compile(
    r"(?im)(?:"
    r"^\s*(?:system|developer|user|assistant)\s*(?:prompt|message)\s*:"
    r"|^\s*(?:task|prompt|question|answer|response|correct\s+answer|user\s+query"
    r"|user\s+request|model\s+responses?|task\s+description)\s*:"
    r"|\byou\s+are\s+(?:a|an)\s+(?:helpful|professional|expert|careful|"
    r"autonomous)?\s*(?:assistant|agent|evaluator|judge|researcher)\b"
    r"|^\s*(?:\*{0,2}critical\*{0,2}\s*:\s*)?you\s+are\b"
    r"|^\s*(?:instructions?|output\s+format)\s*:"
    r"|<(?:user|assistant|system|think)>"
    r")"
)
APPENDIX_SECTION_LINE_RE = re.compile(
    r"(?m)^\s*([A-Z](?:\.\d+)*)\.?\s+([A-Z][^\n]{2,120})\s*$"
)
SOURCE_SECTION_TITLE_RE = re.compile(
    r"(?i)(?:"
    r"\btask\s+examples?\b"
    r"|\bin-domain\s+examples?\b"
    r"|\bout-of-domain\s+examples?\b"
    r"|\bdataset\s+taxonomy\s+and\s+examples?\b"
    r"|\bmeta\s+prompts?\b"
    r"|\binstructions?\s+for\b"
    r"|\bcase\s+stud(?:y|ies)\s+of\s+(?:scientific\s+)?"
    r"(?:judge|thinker|model|agent)\b"
    r")"
)
MARKDOWN_HEADING_RE = re.compile(r"(?m)^\s*#{1,4}\s+\S")
SOURCE_TRAJECTORY_EXAMPLE_RE = re.compile(
    r"(?ims)^\s*example\s+\d+\s*:.*?"
    r"(?:攻击提示|attack\s+prompt).*?"
    r"(?:执行轨迹|trajectory\s*:)"
)
TRANSLATION_REFUSAL_RE = re.compile(
    r"抱歉[，,]?\s*我(?:目前)?(?:无法|不能)"
    r"[^。！？\n]{0,100}(?:查看|访问|翻译|处理|完成|提供)"
)
SCHOLARLY_PROOF_RE = re.compile(
    r"(?im)(?:"
    r"^\s*(?:proof|proposition|theorem|lemma|definition|"
    r"sufficient\s+condition)\b"
    r"|\bthis\s+(?:completes|concludes)\s+the\s+proof\b"
    r")"
)
ACADEMIC_SECTION_HEADING_RE = re.compile(
    r"(?im)^\s*(?:\d+(?:\.\d+)*\.?\s+)?"
    r"(?:conclusions?|discussion|methods?|methodology|results?)\s*$"
)
PARTIAL_SOURCE_HINT_RE = re.compile(
    r"(?im)^\s*(?:"
    r"description|global\s+caption|(?:\[\s*)?shot\s*\d+|"
    r"(?:meta\w*|model)\s+response|original\s+\w+\s+template|"
    r"user|assistant|prompt|question|answer|reasoning|task"
    r")\s*:"
    r"|^\s*(?:\w*claw\s+response\b|original\s+\w+\s+template\b)"
    r"|\[(?:SEG|Shot\s*\d+)\]"
    r"|<(?:think|user|assistant|system)>"
)
IMPERATIVE_EXAMPLE_LINE_RE = re.compile(
    r"(?im)^\s*(?:add|change|make|transform|replace|remove|generate)\s+"
)
NUMBERED_PROCEDURE_LINE_RE = re.compile(
    r"(?i)\b\d+\.\s+(?:create|prepare|open|click|select|set|"
    r"launch|choose|download|install|import|run|use)\b"
)
PARTIAL_VISUAL_BLOCK_RE = re.compile(
    r"(?i)(?:table|figure|表|图)\s*\d+|\bmetric\s+avg\.?\s+time\b"
)
ACADEMIC_NARRATIVE_RE = re.compile(
    r"(?i)\b(?:"
    r"in\s+(?:this|the)\s+(?:paper|work|study)"
    r"|in\s+conclusion"
    r"|we\s+(?:present|propose|introduce|find|demonstrate|show|aim)"
    r"|despite\s+(?:its|the|these)"
    r")\b"
)
SOURCE_DATA_APPENDIX_RE = re.compile(
    r"(?im)(?:"
    r"^\s*(?:[A-Z]\.?\d*\.?\s*)?(?:instruction\s+templates?|"
    r"benchmark\s+examples?|case\s+stud(?:y|ies)|scoring\s+rubrics?)\b"
    r"|\bscore\s+anchor\s+description\b"
    r")"
)
CONTENTS_PAGE_RE = re.compile(r"(?im)^\s*(?:table\s+of\s+)?contents\s*$")
DOT_LEADER_RE = re.compile(r"(?:\.\s*){5,}")
SOURCE_DATA_QA_RE = re.compile(
    r"(?i)(?:"
    r"\bwhich\s+of\s+the\s+following\b"
    r"|\bselect\s+all\s+that\s+apply\b"
    r"|\bcorrect\s+answer\b"
    r"|\boption\s+[A-F]\b"
    r")"
)
IMAGE_PROMPT_RE = re.compile(
    r"(?i)\b(?:illustration|photograph(?:y|ic)?|painting|footage|film|"
    r"render|animation|scene)\b.{0,180}"
    r"\b(?:style|depict(?:ing|s)?|showcas(?:e|es|ing)|featur(?:e|es|ing))\b"
)
SOURCE_TEMPLATE_TERM_RE = re.compile(
    r"(?i)\b(?:prompts?|instructions?|guidelines?|input\s+format|"
    r"output\s+format)\b"
)
SOURCE_TEMPLATE_STRUCTURE_RE = re.compile(
    r"(?is)\bprompts?\b.*\bguidelines?\s*:.*\bexamples?\s*:"
)
FIGURE_FRAME_RE = re.compile(
    r"(?i)\b(?:figure|image|frame\s*\d+)\b"
)
JSON_FIELD_RE = re.compile(r'"[A-Za-z][A-Za-z0-9_-]*"\s*:')
TABLE_HEADING_RE = re.compile(r"(?im)^\s*(?:table|表)\s*\d+")
NUMERIC_TOKEN_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)*(?:%|×)?")
REFERENCE_LIKE_LINE_RE = re.compile(
    r"(?i)(?:^\s*\[\d+\]|\barxiv\b|\bdoi\b|\bproceedings\b|"
    r"\btransactions\b|\bconference\s+on\b|https?://|"
    r"\b(?:19|20)\d{2}\b.*\bet\s+al\b)"
)
NUMBERED_REFERENCE_ENTRY_RE = re.compile(r"^\s*\[\d{1,4}\](?:\s+|$)")


class PdfTextQualityError(RuntimeError):
    """Raised when bounded PDF text extraction cannot be trusted."""


def pdftotext_command(
    pdf_path,
    output_path,
    max_pages=DEFAULT_MAX_PAGES,
    executable="pdftotext",
):
    """Build the non-shell Poppler command used by the audit."""
    return [
        executable,
        "-f",
        "1",
        "-l",
        str(max(1, int(max_pages))),
        "-raw",
        "-enc",
        "UTF-8",
        str(pdf_path),
        str(output_path),
    ]


def extract_pdf_text(
    pdf_path,
    executable="pdftotext",
    max_pages=DEFAULT_MAX_PAGES,
    max_text_bytes=DEFAULT_MAX_TEXT_BYTES,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
):
    """Extract text with explicit page, output-read, and wall-clock limits."""
    resolved = shutil.which(executable)
    if not resolved:
        raise PdfTextQualityError(
            "pdftotext is unavailable; install poppler-utils"
        )
    max_text_bytes = max(1, int(max_text_bytes))
    timeout_seconds = max(1, int(timeout_seconds))
    with tempfile.TemporaryDirectory(prefix="paper-trans-pdf-text-") as tmp:
        output_path = Path(tmp) / "paper.txt"
        command = pdftotext_command(
            pdf_path,
            output_path,
            max_pages=max_pages,
            executable=resolved,
        )
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise PdfTextQualityError(
                "pdftotext timed out after {}s".format(timeout_seconds)
            )
        except OSError as exc:
            raise PdfTextQualityError("pdftotext failed: {}".format(exc))
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        if completed.returncode != 0:
            raise PdfTextQualityError(
                "pdftotext exited {}: {}".format(
                    completed.returncode,
                    stderr[:300] or "no diagnostic",
                )
            )
        if not output_path.is_file():
            raise PdfTextQualityError("pdftotext produced no output file")
        size = output_path.stat().st_size
        if size > max_text_bytes:
            raise PdfTextQualityError(
                "pdftotext output exceeds {} bytes: {}".format(
                    max_text_bytes,
                    size,
                )
            )
        return output_path.read_text(encoding="utf-8", errors="replace")


def _page_sample(page):
    candidates = []
    for line in page.splitlines():
        normalized = " ".join(line.split())
        if len(LETTER_RE.findall(normalized)) < 80:
            continue
        if len(WORD_RE.findall(normalized)) < 12:
            continue
        if REFERENCE_LIKE_LINE_RE.search(normalized):
            continue
        candidates.append(normalized)
        if len(candidates) >= 2:
            break
    if not candidates:
        candidates = [" ".join(page.split())]
    return " ".join(candidates)[:360]


def _longest_consecutive_run(numbers):
    longest = 0
    current = 0
    previous = None
    for number in sorted(numbers):
        if previous is not None and number == previous + 1:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = number
    return longest


def _english_line_run(page):
    """Return the strongest run of wrapped English prose lines."""
    best_lines = 0
    best_words = 0
    best_text = ""
    current = []
    current_words = 0
    for raw_line in page.splitlines():
        line = " ".join(raw_line.split())
        letters = len(LETTER_RE.findall(line))
        cjk = len(CJK_RE.findall(line))
        words = len(WORD_RE.findall(line))
        english_line = (
            letters >= 15
            and words >= 3
            and 100 * cjk / max(1, cjk + letters) < 10.0
        )
        if english_line:
            current.append(line)
            current_words += words
            if (
                len(current) > best_lines
                or (
                    len(current) == best_lines
                    and current_words > best_words
                )
            ):
                best_lines = len(current)
                best_words = current_words
                best_text = " ".join(current)[:360]
        else:
            current = []
            current_words = 0
    return best_lines, best_words, best_text


def _reference_dense(page):
    lines = [
        " ".join(line.split())
        for line in page.splitlines()
        if line.strip()
    ]
    cjk = len(CJK_RE.findall(page))
    letters = len(LETTER_RE.findall(page))
    if 100 * cjk / max(1, cjk + letters) >= 8.0:
        return False
    # Parenthetical citations are common in ordinary Related Work and
    # Introduction prose.  Only use bibliography-entry-shaped line starts as
    # a heading-free fallback; explicit References headings cover other styles.
    numbered_entries = sum(
        1 for line in lines if NUMBERED_REFERENCE_ENTRY_RE.match(line)
    )
    return numbered_entries >= 3


def _substantive_reference_chunk(text):
    return (
        len(LETTER_RE.findall(text)) >= 80
        or len(CJK_RE.findall(text)) >= 30
        or bool(NUMBERED_REFERENCE_ENTRY_RE.search(text))
    )


def _split_chinese_reference_heading_position(page):
    """Recognize duplicated vertical glyph extraction of ``参考文献``.

    Some PDFs render a heading with each glyph in several positioned text
    layers.  Poppler then emits a small block such as ``参 / 参考 / 考文``.
    Similar duplication also occurs inside figure captions, so the block must
    be detached from adjacent prose rather than merely contain the four glyphs.
    """
    lines = page.splitlines(True)
    positions = []
    position = 0
    for raw_line in lines:
        positions.append(position)
        position += len(raw_line)
    normalized = ["".join(line.split()) for line in lines]
    allowed = set("参考文献")
    index = 0
    while index < len(lines):
        if (
            not normalized[index]
            or not set(normalized[index]).issubset(allowed)
        ):
            index += 1
            continue
        end = index
        collapsed = ""
        while (
            end < len(lines)
            and normalized[end]
            and set(normalized[end]).issubset(allowed)
        ):
            collapsed += normalized[end]
            end += 1
        previous = normalized[index - 1] if index else ""
        following = normalized[end] if end < len(lines) else ""
        ordered = (
            collapsed.find("参") >= 0
            and collapsed.find("考", collapsed.find("参") + 1) >= 0
            and collapsed.find("文", collapsed.find("考") + 1) >= 0
            and collapsed.find("献", collapsed.find("文") + 1) >= 0
        )
        detached = (
            (not previous or previous[-1] not in allowed)
            and (not following or following[0] not in allowed)
        )
        if end - index >= 5 and ordered and detached:
            return positions[index]
        index = end
    return None


def _bare_appendix_heading_position(page):
    """Find a top-of-page ``A Title`` heading backed by an ``A.1`` heading.

    Bibliography entries frequently contain title-cased lines such as
    ``A Benchmark for ...``.  Requiring a matching subsection close to the
    page top avoids treating those titles as the end of the references.
    """
    nonempty = []
    position = 0
    for raw_line in page.splitlines(True):
        normalized = " ".join(raw_line.split())
        if normalized:
            nonempty.append((position, normalized))
            if len(nonempty) >= 8:
                break
        position += len(raw_line)
    for index, (start, line) in enumerate(nonempty[:4]):
        heading = BARE_APPENDIX_HEADING_RE.match(line)
        if not heading:
            continue
        letter = heading.group(1)
        for _, following in nonempty[index + 1:]:
            subsection = BARE_APPENDIX_SUBHEADING_RE.match(following)
            if subsection and subsection.group(1) == letter:
                return start
    return None


def _update_source_section(page, current_label):
    """Track explicitly source-oriented appendix sections across pages."""
    for match in APPENDIX_SECTION_LINE_RE.finditer(page):
        label = match.group(1)
        title = match.group(2)
        if current_label:
            is_descendant = (
                label == current_label
                or label.startswith(current_label + ".")
            )
            if (
                not is_descendant
                and len(label.split(".")) <= len(current_label.split("."))
            ):
                current_label = None
        if SOURCE_SECTION_TITLE_RE.search(title):
            current_label = label
    return current_label


def is_untranslated_pdf_prose(report: Dict[str, object]) -> bool:
    """Flag only sustained English-dominant non-reference PDF prose."""
    dominant = int(report.get("english_dominant_pages", 0))
    run = int(report.get("longest_english_page_run", 0))
    analyzed = int(report.get("analyzable_pages", 0))
    cjk_pct = float(report.get("cjk_pct_exact", report.get("cjk_pct", 0.0)))
    fraction = dominant / max(1, analyzed)
    return (
        (dominant >= 3 and cjk_pct < 12.0)
        or (
            dominant >= 3
            and cjk_pct < 25.0
            and fraction >= 0.30
        )
        or run >= 4
        or dominant >= 10
        or (dominant >= 5 and cjk_pct < 30.0 and fraction >= 0.20)
    )


def analyze_pdf_text(text, max_samples=5):
    """Analyze already extracted Poppler text, one form-feed per PDF page."""
    pages = str(text or "").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()

    in_references = False
    reference_pages = []
    source_data_pages = []
    structural_pages = []
    refusal_pages = []
    refusal_samples = []
    dominant_pages = []
    partial_pages = []
    partial_samples = []
    samples = []
    analyzable_pages = 0
    cjk_total = 0
    letter_total = 0

    in_appendix = False
    source_section_label = None
    for page_number, page in enumerate(pages, 1):
        refusal = TRANSLATION_REFUSAL_RE.search(page)
        if refusal:
            refusal_pages.append(page_number)
            if len(refusal_samples) < max(0, int(max_samples)):
                start = max(0, refusal.start() - 80)
                end = min(len(page), refusal.end() + 120)
                refusal_samples.append({
                    "page": page_number,
                    "text": " ".join(page[start:end].split())[:360],
                })
        markers = []
        markers.extend(
            (match.start(), "references")
            for match in REFERENCE_HEADING_RE.finditer(page)
        )
        split_reference_heading = _split_chinese_reference_heading_position(
            page
        )
        if split_reference_heading is not None:
            markers.append((split_reference_heading, "references"))
        markers.extend(
            (match.start(), "appendix")
            for match in APPENDIX_HEADING_RE.finditer(page)
        )
        if in_references and not any(
            marker == "appendix" for _, marker in markers
        ):
            bare_appendix = _bare_appendix_heading_position(page)
            if bare_appendix is not None:
                markers.append((bare_appendix, "appendix"))
        ordered_markers = sorted(set(markers))
        analysis_parts = []
        reference_content = False
        cursor = 0
        reference_state = in_references
        for position, marker in ordered_markers:
            position = max(cursor, min(len(page), position))
            chunk = page[cursor:position]
            if reference_state:
                reference_content = (
                    reference_content
                    or _substantive_reference_chunk(chunk)
                )
            else:
                analysis_parts.append(chunk)
            reference_state = marker == "references"
            if marker == "appendix":
                in_appendix = True
            cursor = position
        remainder = page[cursor:]
        if reference_state:
            reference_content = (
                reference_content
                or _substantive_reference_chunk(remainder)
            )
        else:
            analysis_parts.append(remainder)
        in_references = reference_state
        analysis_page = "".join(analysis_parts)
        if not ordered_markers and not in_references and _reference_dense(page):
            # A citation-dense page without a heading is probably part of the
            # bibliography, but must not force every later survey/body page
            # into reference mode.
            reference_content = True
            analysis_page = ""
        if reference_content:
            reference_pages.append(page_number)
        if not analysis_page.strip():
            continue
        page = analysis_page
        if in_appendix:
            source_section_label = _update_source_section(
                page,
                source_section_label,
            )

        cjk = len(CJK_RE.findall(page))
        letters = len(LETTER_RE.findall(page))
        words = len(WORD_RE.findall(page))
        structural = (
            bool(CONTENTS_PAGE_RE.search(page))
            or len(DOT_LEADER_RE.findall(page)) >= 8
            or (
                bool(TABLE_HEADING_RE.search(page))
                and len(NUMERIC_TOKEN_RE.findall(page)) >= 35
            )
        )
        if structural:
            structural_pages.append(page_number)
            continue
        if cjk + letters < 300 or (words < 30 and cjk < 100):
            continue
        cjk_pct = 100 * cjk / max(1, cjk + letters)
        source_markers = len(SOURCE_DATA_MARKER_RE.findall(page))
        qa_markers = len(SOURCE_DATA_QA_RE.findall(page))
        image_prompt_markers = len(IMAGE_PROMPT_RE.findall(page))
        template_terms = len(SOURCE_TEMPLATE_TERM_RE.findall(page))
        json_fields = len(JSON_FIELD_RE.findall(page))
        source_data = cjk_pct < 20.0 and (
            bool(source_section_label)
            or source_markers >= 2
            or len(MARKDOWN_HEADING_RE.findall(page)) >= 4
            or bool(SOURCE_TRAJECTORY_EXAMPLE_RE.search(page))
            or (
                template_terms >= 3
                and bool(SOURCE_TEMPLATE_STRUCTURE_RE.search(page))
            )
            or json_fields >= 6
            or (
                image_prompt_markers >= 2
                and bool(FIGURE_FRAME_RE.search(page))
            )
            or (
                in_appendix
                and (
                    source_markers >= 1
                    or qa_markers >= 1
                    or image_prompt_markers >= 2
                    or (
                        template_terms >= 3
                        and bool(SOURCE_TEMPLATE_STRUCTURE_RE.search(page))
                    )
                )
            )
            or (
                in_appendix
                and bool(SOURCE_DATA_APPENDIX_RE.search(page[:2000]))
            )
        )
        if source_data:
            source_data_pages.append(page_number)
            continue

        analyzable_pages += 1
        cjk_total += cjk
        letter_total += letters

        english_dominant = (
            letters >= 700
            and words >= 100
            and cjk_pct < 20.0
        )
        if english_dominant:
            dominant_pages.append(page_number)
            if len(samples) < max(0, int(max_samples)):
                samples.append({
                    "page": page_number,
                    "cjk_pct": round(cjk_pct, 1),
                    "english_words": words,
                    "text": _page_sample(page),
                })
        line_run, line_words, line_sample = _english_line_run(page)
        partial_source_like = (
            source_markers >= 1
            or bool(PARTIAL_SOURCE_HINT_RE.search(page))
            or len(IMPERATIVE_EXAMPLE_LINE_RE.findall(page)) >= 3
            or len(NUMBERED_PROCEDURE_LINE_RE.findall(page)) >= 3
            or bool(PARTIAL_VISUAL_BLOCK_RE.search(line_sample[:160]))
        )
        partial_reason = None
        if english_dominant and SCHOLARLY_PROOF_RE.search(page):
            partial_reason = "scholarly_proof"
        elif (
            not partial_source_like
            and
            ACADEMIC_SECTION_HEADING_RE.search(page)
            and line_run >= 3
            and line_words >= 30
            and (
                len(re.findall(r"[.!?](?:\s|$)", line_sample)) >= 2
                or bool(ACADEMIC_NARRATIVE_RE.search(line_sample))
            )
        ):
            partial_reason = "english_paragraph_in_academic_section"
        if partial_reason:
            partial_pages.append(page_number)
            if len(partial_samples) < max(0, int(max_samples)):
                partial_samples.append({
                    "page": page_number,
                    "reason": partial_reason,
                    "text": line_sample or _page_sample(page),
                })

    total_letters = cjk_total + letter_total
    cjk_pct_exact = 100 * cjk_total / max(1, total_letters)
    report = {
        "policy_version": PDF_TEXT_POLICY_VERSION,
        "pages_scanned": len(pages),
        "analyzable_pages": analyzable_pages,
        "reference_pages": len(reference_pages),
        "reference_page_numbers": reference_pages,
        "source_data_pages": len(source_data_pages),
        "source_data_page_numbers": source_data_pages,
        "structural_pages": len(structural_pages),
        "structural_page_numbers": structural_pages,
        "translation_refusal_pages": len(refusal_pages),
        "translation_refusal_page_numbers": refusal_pages,
        "translation_refusal_samples": refusal_samples,
        "cjk": cjk_total,
        "letters": letter_total,
        "cjk_pct": round(cjk_pct_exact, 1),
        "cjk_pct_exact": cjk_pct_exact,
        "english_dominant_pages": len(dominant_pages),
        "english_dominant_page_numbers": dominant_pages,
        "longest_english_page_run": _longest_consecutive_run(dominant_pages),
        "partial_untranslated_prose_pages": len(partial_pages),
        "partial_untranslated_prose_page_numbers": partial_pages,
        "partial_untranslated_prose_samples": partial_samples,
        "samples": samples,
    }
    report["untranslated_prose"] = is_untranslated_pdf_prose(report)
    return report


def analyze_pdf(
    pdf_path,
    executable="pdftotext",
    max_pages=DEFAULT_MAX_PAGES,
    max_text_bytes=DEFAULT_MAX_TEXT_BYTES,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
):
    """Extract and analyze one PDF under the configured resource bounds."""
    max_pages = max(1, int(max_pages))
    # Extract one sentinel page so an exactly-N-page PDF is distinguishable
    # from a longer PDF truncated at the configured page boundary.
    text = extract_pdf_text(
        pdf_path,
        executable=executable,
        max_pages=max_pages + 1,
        max_text_bytes=max_text_bytes,
        timeout_seconds=timeout_seconds,
    )
    extracted_pages = str(text or "").split("\f")
    if extracted_pages and not extracted_pages[-1].strip():
        extracted_pages.pop()
    page_limit_reached = len(extracted_pages) > max_pages
    report = analyze_pdf_text("\f".join(extracted_pages[:max_pages]))
    report["path"] = str(pdf_path)
    report["page_limit_reached"] = page_limit_reached
    return report


def analyze_pdf_cached(
    pdf_path,
    cache_dir,
    executable="pdftotext",
    max_pages=DEFAULT_MAX_PAGES,
    max_text_bytes=DEFAULT_MAX_TEXT_BYTES,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
):
    """Reuse metrics when the PDF, policy, and extraction bounds are unchanged."""
    pdf_path = Path(pdf_path)
    cache_root = Path(cache_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / (pdf_path.stem + ".json")
    stat = pdf_path.stat()
    signature = {
        "policy_version": PDF_TEXT_POLICY_VERSION,
        "pdf_size": stat.st_size,
        "pdf_mtime_ns": getattr(
            stat,
            "st_mtime_ns",
            int(stat.st_mtime * 1_000_000_000),
        ),
        "max_pages": int(max_pages),
        "max_text_bytes": int(max_text_bytes),
    }
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            isinstance(cached, dict)
            and cached.get("signature") == signature
            and isinstance(cached.get("report"), dict)
        ):
            report = dict(cached["report"])
            report["_cache_hit"] = True
            return report
    except (OSError, ValueError):
        pass

    report = analyze_pdf(
        pdf_path,
        executable=executable,
        max_pages=max_pages,
        max_text_bytes=max_text_bytes,
        timeout_seconds=timeout_seconds,
    )
    payload = {"signature": signature, "report": report}
    temporary = cache_path.with_name(
        "{}.{}.tmp".format(cache_path.name, os.getpid())
    )
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(str(temporary), str(cache_path))
    report = dict(report)
    report["_cache_hit"] = False
    return report
