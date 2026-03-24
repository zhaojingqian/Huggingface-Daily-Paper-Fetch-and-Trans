#!/usr/bin/env python3
"""
Patch find_main_tex_file in latex_toolbox.py:
1. Check 00README.json (arxiv standard) for toplevel file first
2. Add bonus for root-level files (less likely to be subdirectory drafts)

This fixes cases where the project contains auxiliary tex files in subdirectories
(like others/longcat.tex) that score higher than the real main file at root.
"""
import sys

path = '/gpt/crazy_functions/latex_fns/latex_toolbox.py'
with open(path, 'r') as f:
    src = f.read()

if '00README.json' in src:
    print('Already patched, skipping')
    sys.exit(0)

old = '''def find_main_tex_file(file_manifest, mode):
    """
    在多Tex文档中，寻找主文件，必须包含documentclass，返回找到的第一个。
    P.S. 但愿没人把latex模板放在里面传进来 (6.25 加入判定latex模板的代码)
    """
    candidates = []
    for texf in file_manifest:
        if os.path.basename(texf).startswith("merge"):
            continue
        with open(texf, "r", encoding="utf8", errors="ignore") as f:
            file_content = f.read()
        if r"\\documentclass" in file_content:
            candidates.append(texf)
        else:
            continue

    if len(candidates) == 0:
        raise RuntimeError("无法找到一个主Tex文件（包含documentclass关键字）")
    elif len(candidates) == 1:
        return candidates[0]
    else:  # if len(candidates) >= 2 通过一些Latex模板中常见（但通常不会出现在正文）的单词，对不同latex源文件扣分，取评分最高者返回
        candidates_score = []
        # 给出一些判定模板文档的词作为扣分项
        unexpected_words = [
            "\\\\LaTeX",
            "manuscript",
            "Guidelines",
            "font",
            "citations",
            "rejected",
            "blind review",
            "reviewers",
        ]
        expected_words = ["\\\\input", "\\\\ref", "\\\\cite"]
        for texf in candidates:
            candidates_score.append(0)
            with open(texf, "r", encoding="utf8", errors="ignore") as f:
                file_content = f.read()
                file_content = rm_comments(file_content)
            for uw in unexpected_words:
                if uw in file_content:
                    candidates_score[-1] -= 1
            for uw in expected_words:
                if uw in file_content:
                    candidates_score[-1] += 1
        select = np.argmax(candidates_score)  # 取评分最高者返回
        return candidates[select]'''

# Verify old text is in file
if old not in src:
    print('ERROR: old block not found in file')
    # Try to find approximate location
    idx = src.find('def find_main_tex_file')
    if idx >= 0:
        print('Found function at idx', idx)
        print(repr(src[idx:idx+200]))
    sys.exit(1)

new = '''def find_main_tex_file(file_manifest, mode):
    """
    在多Tex文档中，寻找主文件，必须包含documentclass，返回找到的第一个。
    P.S. 但愿没人把latex模板放在里面传进来 (6.25 加入判定latex模板的代码)
    P.S. 优先读取 00README.json（arXiv 标准）确定主文件；其次偏好根目录文件
    """
    # ── Step 0: check 00README.json for explicit toplevel declaration ──────
    # arXiv packages often include a 00README.json with "usage":"toplevel"
    if file_manifest:
        project_root = os.path.dirname(file_manifest[0])
        # Find the common root among all files
        if len(file_manifest) > 1:
            try:
                import os.path as _osp
                project_root = _osp.commonpath([_osp.dirname(f) for f in file_manifest])
            except Exception:
                pass
        readme_json = os.path.join(project_root, '00README.json')
        if os.path.exists(readme_json):
            try:
                import json as _json
                with open(readme_json, 'r', encoding='utf8', errors='ignore') as _f:
                    _readme = _json.load(_f)
                for _entry in _readme.get('sources', []):
                    if _entry.get('usage') == 'toplevel':
                        _toplevel = os.path.join(project_root, _entry['filename'])
                        if os.path.exists(_toplevel):
                            return _toplevel
            except Exception:
                pass

    candidates = []
    for texf in file_manifest:
        if os.path.basename(texf).startswith("merge"):
            continue
        with open(texf, "r", encoding="utf8", errors="ignore") as f:
            file_content = f.read()
        if r"\\documentclass" in file_content:
            candidates.append(texf)
        else:
            continue

    if len(candidates) == 0:
        raise RuntimeError("无法找到一个主Tex文件（包含documentclass关键字）")
    elif len(candidates) == 1:
        return candidates[0]
    else:  # if len(candidates) >= 2 通过一些Latex模板中常见（但通常不会出现在正文）的单词，对不同latex源文件扣分，取评分最高者返回
        # Determine project root for depth scoring
        try:
            import os.path as _osp
            _common = _osp.commonpath(candidates)
        except Exception:
            _common = ''
        candidates_score = []
        # 给出一些判定模板文档的词作为扣分项
        unexpected_words = [
            "\\\\LaTeX",
            "manuscript",
            "Guidelines",
            "font",
            "citations",
            "rejected",
            "blind review",
            "reviewers",
        ]
        expected_words = ["\\\\input", "\\\\ref", "\\\\cite"]
        for texf in candidates:
            candidates_score.append(0)
            with open(texf, "r", encoding="utf8", errors="ignore") as f:
                file_content = f.read()
                file_content = rm_comments(file_content)
            for uw in unexpected_words:
                if uw in file_content:
                    candidates_score[-1] -= 1
            for uw in expected_words:
                if uw in file_content:
                    candidates_score[-1] += 1
            # Prefer root-level files: penalize files in subdirectories
            # A file directly in project root has depth 0 relative to common path
            rel = os.path.relpath(texf, _common) if _common else texf
            depth = rel.count(os.sep)
            if depth > 0:
                candidates_score[-1] -= depth  # penalize deeper files
        select = np.argmax(candidates_score)  # 取评分最高者返回
        return candidates[select]'''

src = src.replace(old, new, 1)
with open(path, 'w') as f:
    f.write(src)
print('Patched find_main_tex_file: 00README.json check + depth penalty')
