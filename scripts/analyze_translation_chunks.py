#!/usr/bin/env python3
"""Summarize splitter decisions and unchanged English chunks for one paper."""

import argparse
import html
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paperhub.translation_quality import analyze_tex, is_untranslated_prose


NODE_RE = re.compile(
    r'<p style="color:(?P<color>red|black);">(?P<body>.*?)</p>',
    re.DOTALL,
)
RANGE_RE = re.compile(r"^#\[[^\]]+\]")
COMMAND_RE = re.compile(r"^\s*\\([A-Za-z@]+)")
LATEX_COMMAND_RE = re.compile(
    r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?"
)
LONG_ENGLISH_RE = re.compile(r"(?:[A-Za-z][A-Za-z-]{2,}[\s,.;:()]+){8,}")


def decode_node(body):
    text = html.unescape(body.replace("<br/>", "\n"))
    text = RANGE_RE.sub("", text)
    if text.endswith("#"):
        text = text[:-1]
    return text


def percentile(values, ratio):
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * ratio))]


def analyze(debug_html, translated_tex):
    debug = Path(debug_html).read_text(encoding="utf-8", errors="replace")
    translated = Path(translated_tex).read_text(encoding="utf-8", errors="replace")
    nodes = [
        (match.group("color"), decode_node(match.group("body")))
        for match in NODE_RE.finditer(debug)
    ]
    transform = [text for color, text in nodes if color == "black"]
    preserve = [text for color, text in nodes if color == "red"]
    lengths = [len(text) for text in transform]
    commands = Counter()
    unchanged = []
    for text in transform:
        command = COMMAND_RE.match(text)
        if command:
            commands[command.group(1)] += 1
        core = text.strip()
        rough = LATEX_COMMAND_RE.sub(" ", core)
        english_letters = len(re.findall(r"[A-Za-z]", rough))
        cjk = len(re.findall(r"[\u4e00-\u9fff]", rough))
        if (
            len(core) >= 30
            and english_letters >= 18
            and cjk < 4
            and core in translated
        ):
            unchanged.append(core)

    english_lines = [
        {"line": line_no, "text": line.strip()[:300]}
        for line_no, line in enumerate(translated.splitlines(), 1)
        if LONG_ENGLISH_RE.search(LATEX_COMMAND_RE.sub(" ", line))
    ]
    return {
        "debug_html": str(debug_html),
        "translated_tex": str(translated_tex),
        "nodes": {
            "total": len(nodes),
            "transform": len(transform),
            "preserve": len(preserve),
        },
        "transform_length": {
            "min": min(lengths) if lengths else 0,
            "median": statistics.median(lengths) if lengths else 0,
            "p90": percentile(lengths, 0.9),
            "max": max(lengths) if lengths else 0,
            "under_80": sum(length < 80 for length in lengths),
            "under_160": sum(length < 160 for length in lengths),
        },
        "transform_command_prefixes": dict(commands.most_common(20)),
        "unchanged_transform_chunks": len(unchanged),
        "unchanged_samples": [re.sub(r"\s+", " ", item)[:300] for item in unchanged[:12]],
        "long_english_lines": len(english_lines),
        "long_english_samples": english_lines[:12],
    }


def analyze_debug_root(debug_root, translation_dirs):
    reports = []
    for debug_path in sorted(Path(debug_root).glob("**/debug_log.html")):
        arxiv_id = debug_path.parents[1].name
        tex_path = None
        for directory in translation_dirs:
            candidate = Path(directory) / f"{arxiv_id}_merge_translate_zh.tex"
            if candidate.is_file():
                tex_path = candidate
                break
        if not tex_path:
            continue
        report = analyze(debug_path, tex_path)
        report["arxiv_id"] = arxiv_id
        reports.append(report)

    commands = Counter()
    for report in reports:
        commands.update(report["transform_command_prefixes"])
    total_transform = sum(item["nodes"]["transform"] for item in reports)
    total_unchanged = sum(item["unchanged_transform_chunks"] for item in reports)
    return {
        "papers": len(reports),
        "transform_chunks": total_transform,
        "transform_under_80": sum(
            item["transform_length"]["under_80"] for item in reports
        ),
        "transform_under_160": sum(
            item["transform_length"]["under_160"] for item in reports
        ),
        "unchanged_transform_chunks": total_unchanged,
        "unchanged_pct": round(100 * total_unchanged / max(1, total_transform), 1),
        "transform_command_prefixes": dict(commands.most_common()),
        "papers_detail": [
            {
                "arxiv_id": item["arxiv_id"],
                "transform": item["nodes"]["transform"],
                "median_length": item["transform_length"]["median"],
                "under_160": item["transform_length"]["under_160"],
                "unchanged": item["unchanged_transform_chunks"],
                "long_english_lines": item["long_english_lines"],
            }
            for item in sorted(
                reports,
                key=lambda value: value["unchanged_transform_chunks"],
                reverse=True,
            )
        ],
    }


def analyze_tex_dirs(directories):
    paths = []
    for directory in directories:
        paths.extend(Path(directory).glob("*_merge_translate_zh.tex"))
    papers = [analyze_tex(path) for path in sorted(set(paths))]
    environments = Counter()
    commands = Counter()
    english_words = Counter()
    word_line_bins = Counter()
    for paper in papers:
        environments.update(paper["by_environment"])
        commands.update(paper["by_command"])
        english_words.update(paper["_english_word_counts"])
        word_line_bins.update(paper["english_word_line_bins"])
        paper.pop("_english_word_counts", None)
    affected = [paper for paper in papers if paper["long_english_lines"]]
    broad_affected = [
        paper for paper in papers if paper["broad_english_lines"]
    ]
    cjk_bins = Counter()
    english_bins = Counter()
    broad_english_bins = Counter()
    for paper in papers:
        cjk = paper["cjk_pct"]
        cjk_bins[
            "<20" if cjk < 20 else
            "20-39" if cjk < 40 else
            "40-59" if cjk < 60 else
            "60-79" if cjk < 80 else
            "80+"
        ] += 1
        english = paper["long_english_lines"]
        english_bins[
            "0" if english == 0 else
            "1-5" if english <= 5 else
            "6-20" if english <= 20 else
            "21-50" if english <= 50 else
            "51+"
        ] += 1
        broad_english = paper["broad_english_lines"]
        broad_english_bins[
            "0" if broad_english == 0 else
            "1-5" if broad_english <= 5 else
            "6-20" if broad_english <= 20 else
            "21-50" if broad_english <= 50 else
            "51+"
        ] += 1
    return {
        "papers": len(papers),
        "papers_with_long_english": len(affected),
        "papers_without_long_english": len(papers) - len(affected),
        "long_english_lines": sum(item["long_english_lines"] for item in papers),
        "papers_with_broad_english": len(broad_affected),
        "broad_english_lines": sum(
            item["broad_english_lines"] for item in papers
        ),
        "mixed_language_lines": sum(item["mixed_language_lines"] for item in papers),
        "english_dominant_lines": sum(
            item["english_dominant_lines"] for item in papers
        ),
        "english_word_occurrences": sum(
            item["english_word_occurrences"] for item in papers
        ),
        "english_word_line_bins": dict(word_line_bins),
        "top_english_words": dict(english_words.most_common(50)),
        "cjk_pct_bins": dict(cjk_bins),
        "long_english_line_bins": dict(english_bins),
        "broad_english_line_bins": dict(broad_english_bins),
        "severe_papers": sum(
            is_untranslated_prose(item)
            for item in papers
        ),
        "by_environment": dict(environments.most_common()),
        "by_command": dict(commands.most_common()),
        "worst_papers": sorted(
            broad_affected,
            key=lambda item: (item["broad_english_lines"], -item["cjk_pct"]),
            reverse=True,
        )[:30],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debug-html")
    parser.add_argument("--debug-root")
    parser.add_argument("--translated-tex")
    parser.add_argument("--tex-dir", action="append", default=[])
    parser.add_argument("--translation-dir", action="append", default=[])
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    if args.debug_root:
        report = analyze_debug_root(args.debug_root, args.translation_dir)
    elif args.tex_dir:
        report = analyze_tex_dirs(args.tex_dir)
    elif args.debug_html and args.translated_tex:
        report = analyze(args.debug_html, args.translated_tex)
    else:
        parser.error("use --tex-dir, or both --debug-html and --translated-tex")
    if args.summary_only:
        report.pop("worst_papers", None)
        report.pop("papers_detail", None)
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
