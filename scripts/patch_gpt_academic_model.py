#!/usr/bin/env python3
"""Register the production relay model in gpt-academic's own model catalog.

The slim image is assembled from the running gpt-academic source rather than
vendoring that external repository.  This small, idempotent source overlay is
therefore the single integration boundary: the model is a real catalog entry
and key routing recognizes it directly.  The translation driver must not
alias it to another model at runtime.
"""

from __future__ import annotations

import argparse
from pathlib import Path


MODEL = "deepseek-v4-flash-0731"


MODEL_BLOCK = '''    "deepseek-v4-flash-0731": {
        "fn_with_ui": chatgpt_ui,
        "fn_without_ui": chatgpt_noui,
        "can_multi_thread": True,
        "endpoint": openai_endpoint,
        "max_token": 128000,
        "tokenizer": tokenizer_gpt4,
        "token_cnt": get_token_num_gpt4,
    },

'''


def patch_model_catalog(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if f'"{MODEL}":' in text:
        return False
    marker = '    "gpt-4.1-mini":{'
    if marker not in text:
        raise RuntimeError(f"model catalog marker missing: {path}")
    path.write_text(text.replace(marker, MODEL_BLOCK + marker, 1), encoding="utf-8")
    return True


def patch_key_router(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    marker = "    if llm_model.startswith('gpt-') or llm_model.startswith('chatgpt-') or \\\n"
    if "llm_model.startswith('deepseek-')" in text:
        return False
    if marker not in text:
        raise RuntimeError(f"OpenAI-compatible key-router marker missing: {path}")
    replacement = (
        "    if llm_model.startswith('deepseek-') or "
        "llm_model.startswith('gpt-') or llm_model.startswith('chatgpt-') or \\\n"
    )
    path.write_text(text.replace(marker, replacement, 1), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/gpt")
    args = parser.parse_args()
    root = Path(args.root)
    catalog = root / "request_llms" / "bridge_all.py"
    key_router = root / "shared_utils" / "key_pattern_manager.py"
    changed = [
        patch_model_catalog(catalog),
        patch_key_router(key_router),
    ]
    print(
        f"gpt-academic source model overlay: model_catalog={changed[0]} "
        f"key_router={changed[1]} model={MODEL}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
