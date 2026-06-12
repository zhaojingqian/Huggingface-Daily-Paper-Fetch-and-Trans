#!/usr/bin/env bash
# setup_docker_env.sh — 修复 gpt-academic-latex 容器的 LaTeX 编译环境
#
# 解决问题：
#   1. Noto Sans SemiBold 缺失（2603.16859 类论文使用 mac_automl 模板）
#   2. fontset=windows 注入 Windows 专有 CJK 字体（SimSun/SimHei/KaiTi）在 Linux 不存在
#   3. bxcoloremoji 包缺失（部分论文使用 emoji）
#   4. fontawesome/fontawesome5/fontawesome6/bbding/inconsolata/libertine/newtxmath/zlmtt 包在精简 TeX 环境中缺失
#   5. ctex 包不在 xelatex 检测列表导致使用 pdflatex 编译失败
#   6. \def\input@path 自定义路径无法被 gpt-academic 解析，导致多级目录论文 merge 失败
#
# 使用场景：
#   - 容器首次创建后
#   - 容器被删除重建后（docker run / docker-compose up）
#   - 日常 docker restart 不需要重跑（改动已持久化在容器文件系统内）
#
# 用法：
#   bash /root/workspace/paper-trans/scripts/setup_docker_env.sh
#
set -e

CONTAINER="${GPT_ACADEMIC_CONTAINER:-gpt-academic-latex}"

echo "=== [setup_docker_env] 检查容器 $CONTAINER 是否运行 ==="
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "❌ 容器 $CONTAINER 未运行，请先启动容器"
    exit 1
fi

# ── 1. 安装字体包 ────────────────────────────────────────────────────────────
echo "=== [1/10] 安装 Noto CJK + Arphic + Noto Extra 字体 ==="
docker exec -u root "$CONTAINER" apt-get update -qq
docker exec -u root "$CONTAINER" apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    fonts-noto-extra \
    fonts-arphic-ukai \
    fonts-arphic-uming \
    texlive-lang-european \
    texlive-science

# ── 2. 写入 fontconfig 映射（Windows CJK 字体名 → Linux 等效字体）────────────
echo "=== [2/10] 写入 fontconfig 映射规则 ==="
docker exec -u root "$CONTAINER" bash -c "cat > /etc/fonts/conf.d/99-latex-win-cjk-alias.conf << 'XMLEOF'
<?xml version=\"1.0\"?>
<!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">
<fontconfig>
  <!-- Remap Windows CJK font names to Linux equivalents.
       Used for xelatex papers that bundle Windows CJK font names via ctex. -->
  <match target=\"pattern\">
    <test qual=\"any\" name=\"family\"><string>SimSun</string></test>
    <edit name=\"family\" mode=\"assign\" binding=\"strong\"><string>Noto Serif CJK SC</string></edit>
  </match>
  <match target=\"pattern\">
    <test qual=\"any\" name=\"family\"><string>NSimSun</string></test>
    <edit name=\"family\" mode=\"assign\" binding=\"strong\"><string>Noto Serif CJK SC</string></edit>
  </match>
  <match target=\"pattern\">
    <test qual=\"any\" name=\"family\"><string>SimHei</string></test>
    <edit name=\"family\" mode=\"assign\" binding=\"strong\"><string>Noto Sans CJK SC</string></edit>
  </match>
  <match target=\"pattern\">
    <test qual=\"any\" name=\"family\"><string>KaiTi</string></test>
    <edit name=\"family\" mode=\"assign\" binding=\"strong\"><string>AR PL UKai CN</string></edit>
  </match>
  <match target=\"pattern\">
    <test qual=\"any\" name=\"family\"><string>FangSong</string></test>
    <edit name=\"family\" mode=\"assign\" binding=\"strong\"><string>AR PL UMing CN</string></edit>
  </match>
  <match target=\"pattern\">
    <test qual=\"any\" name=\"family\"><string>Microsoft YaHei</string></test>
    <edit name=\"family\" mode=\"assign\" binding=\"strong\"><string>Noto Sans CJK SC</string></edit>
  </match>
  <match target=\"pattern\">
    <test qual=\"any\" name=\"family\"><string>STSong</string></test>
    <edit name=\"family\" mode=\"assign\" binding=\"strong\"><string>Noto Serif CJK SC</string></edit>
  </match>
  <match target=\"pattern\">
    <test qual=\"any\" name=\"family\"><string>STHeiti</string></test>
    <edit name=\"family\" mode=\"assign\" binding=\"strong\"><string>Noto Sans CJK SC</string></edit>
  </match>
</fontconfig>
XMLEOF"

docker exec -u root "$CONTAINER" fc-cache -fv 2>&1 | tail -3

# ── 3. 安装 bxcoloremoji LaTeX 包 ──────────────────────────────────────────
echo "=== [3/10] 安装 bxcoloremoji LaTeX 包 ==="
ALREADY=$(docker exec "$CONTAINER" kpsewhich bxcoloremoji.sty 2>/dev/null || true)
if [ -n "$ALREADY" ]; then
    echo "  bxcoloremoji 已安装，跳过"
else
    docker exec -u root "$CONTAINER" bash -c "
        cd /tmp && \
        rm -rf bxcoloremoji bxcoloremoji.zip && \
        for url in \
            'https://ctan.math.illinois.edu/macros/latex/contrib/bxcoloremoji.zip' \
            'https://mirrors.mit.edu/CTAN/macros/latex/contrib/bxcoloremoji.zip' \
            'https://mirrors.ctan.org/macros/latex/contrib/bxcoloremoji.zip'; do \
            echo \"  downloading bxcoloremoji: \$url\"; \
            if curl -fL --retry 3 --retry-delay 2 --connect-timeout 20 \"\$url\" -o bxcoloremoji.zip; then \
                break; \
            fi; \
        done && \
        test -s bxcoloremoji.zip && \
        unzip -q bxcoloremoji.zip && \
        TEXDIR=\$(kpsewhich -var-value TEXMFLOCAL) && \
        mkdir -p \${TEXDIR}/tex/latex/bxcoloremoji && \
        cp bxcoloremoji/bxcoloremoji.sty bxcoloremoji/bxcoloremoji-names.def \${TEXDIR}/tex/latex/bxcoloremoji/ && \
        mktexlsr \${TEXDIR} 2>&1 | tail -2 && \
        echo 'bxcoloremoji installed to' \${TEXDIR}/tex/latex/bxcoloremoji/
    "
fi

# ── 4. 安装轻量 stub（精简 TeX 环境可能无图标/字体包）────────────────────
echo "=== [4/10] 安装 fontawesome/bbding/inconsolata/libertine/newtxmath/zlmtt stubs ==="
if docker exec "$CONTAINER" kpsewhich fontawesome.sty >/dev/null 2>&1 \
    && docker exec "$CONTAINER" kpsewhich fontawesome5.sty >/dev/null 2>&1 \
    && docker exec "$CONTAINER" kpsewhich fontawesome6.sty >/dev/null 2>&1 \
    && docker exec "$CONTAINER" kpsewhich bbding.sty >/dev/null 2>&1 \
    && docker exec "$CONTAINER" kpsewhich inconsolata.sty >/dev/null 2>&1 \
    && docker exec "$CONTAINER" kpsewhich libertine.sty >/dev/null 2>&1 \
    && docker exec "$CONTAINER" kpsewhich newtxmath.sty >/dev/null 2>&1 \
    && docker exec "$CONTAINER" kpsewhich zlmtt.sty >/dev/null 2>&1 \
    && docker exec "$CONTAINER" kpsewhich Inconsolatazi4-Regular.otf >/dev/null 2>&1; then
    echo "  fontawesome/fontawesome5/fontawesome6/bbding/inconsolata/libertine/newtxmath/zlmtt 已安装，跳过"
else
    docker exec -u root "$CONTAINER" bash -c "
        TEXDIR=\$(kpsewhich -var-value TEXMFLOCAL)
        mkdir -p \${TEXDIR}/tex/latex/fontawesome5
        cat > \${TEXDIR}/tex/latex/fontawesome5/fontawesome5.sty << 'STYEOF'
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{fontawesome5}[2024/01/01 fontawesome5 stub]
% Icon glyphs are cosmetic for translated paper PDFs; provide no-op fallbacks.
\providecommand{\faIcon}[1]{}
\providecommand{\faicon}[1]{}
\providecommand{\faGithub}{}
\providecommand{\faTwitter}{}
\providecommand{\faEnvelope}{}
\providecommand{\faHome}{}
\providecommand{\faExternalLinkAlt}{}
\providecommand{\faFilePdf}{}
STYEOF
        mkdir -p \${TEXDIR}/tex/latex/fontawesome
        cat > \${TEXDIR}/tex/latex/fontawesome/fontawesome.sty << 'STYEOF'
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{fontawesome}[2024/01/01 fontawesome v4 stub]
% Icon glyphs are cosmetic for translated paper PDFs; provide no-op fallbacks.
\RequirePackage{fontawesome5}
\providecommand{\faIcon}[1]{}
\providecommand{\faicon}[1]{}
\providecommand{\faGithub}{}
\providecommand{\faTwitter}{}
\providecommand{\faEnvelope}{}
\providecommand{\faHome}{}
\providecommand{\faExternalLink}{}
\providecommand{\faFilePdfO}{}
STYEOF
        mkdir -p \${TEXDIR}/tex/latex/fontawesome6
        cat > \${TEXDIR}/tex/latex/fontawesome6/fontawesome6.sty << 'STYEOF'
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{fontawesome6}[2024/01/01 fontawesome6-stub via fontawesome5]
% fontawesome6 is not available in older or slim TeX Live images; fall back to fontawesome5.
\RequirePackage{fontawesome5}
\providecommand{\faIcon}[1]{}
\providecommand{\faicon}[1]{}
STYEOF
        cat > \${TEXDIR}/tex/latex/fontawesome6/fontawesome6-generic.sty << 'STYEOF'
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{fontawesome6-generic}[2024/01/01 fontawesome6-generic stub]
\RequirePackage{fontawesome6}
STYEOF
        mkdir -p \${TEXDIR}/tex/latex/bbding
        cat > \${TEXDIR}/tex/latex/bbding/bbding.sty << 'STYEOF'
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{bbding}[2024/01/01 bbding stub]
% bbding icons are cosmetic in translated paper PDFs; provide common no-op fallbacks.
\providecommand{\Checkmark}{}
\providecommand{\XSolidBrush}{}
\providecommand{\HandRight}{}
\providecommand{\FiveStar}{}
\providecommand{\Envelope}{}
\providecommand{\ScissorRight}{}
\providecommand{\PencilRight}{}
STYEOF
        mkdir -p \${TEXDIR}/tex/latex/inconsolata
        cat > \${TEXDIR}/tex/latex/inconsolata/inconsolata.sty << 'STYEOF'
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{inconsolata}[2024/01/01 inconsolata stub]
% Keep slim images small by mapping the cosmetic monospace font to Latin Modern Mono.
\DeclareOption*{}
\ProcessOptions\relax
\RequirePackage{lmodern}
\renewcommand{\ttdefault}{lmtt}
STYEOF
        mkdir -p \${TEXDIR}/tex/latex/libertine
        cat > \${TEXDIR}/tex/latex/libertine/libertine.sty << 'STYEOF'
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{libertine}[2024/01/01 libertine stub]
% Keep slim images small by mapping the cosmetic Libertine family to Latin Modern.
\DeclareOption*{}
\ProcessOptions\relax
\RequirePackage{lmodern}
\renewcommand{\rmdefault}{lmr}
\renewcommand{\sfdefault}{lmss}
\renewcommand{\ttdefault}{lmtt}
STYEOF
        mkdir -p \${TEXDIR}/tex/latex/newtx
        cat > \${TEXDIR}/tex/latex/newtx/newtxmath.sty << 'STYEOF'
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{newtxmath}[2024/01/01 newtxmath stub]
% Preserve compile compatibility without pulling texlive-fonts-extra into the slim image.
\DeclareOption*{}
\ProcessOptions\relax
\RequirePackage{amsmath}
\RequirePackage{amssymb}
\RequirePackage{mathrsfs}
\RequirePackage{bm}
STYEOF
        mkdir -p \${TEXDIR}/tex/latex/zlmtt
        cat > \${TEXDIR}/tex/latex/zlmtt/zlmtt.sty << 'STYEOF'
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{zlmtt}[2024/01/01 zlmtt stub]
% zlmtt is a cosmetic typewriter font package; use Latin Modern Mono in slim images.
\DeclareOption*{}
\ProcessOptions\relax
\RequirePackage{lmodern}
\renewcommand{\ttdefault}{lmtt}
STYEOF
        FONTDIR=\${TEXDIR}/fonts/opentype/public/inconsolatazi4
        mkdir -p \${FONTDIR}
        REG=\$(kpsewhich lmmono10-regular.otf)
        BOLD=\$(kpsewhich lmmonolt10-bold.otf || kpsewhich lmmono10-regular.otf)
        IT=\$(kpsewhich lmmono10-italic.otf || kpsewhich lmmono10-regular.otf)
        BOLDIT=\$(kpsewhich lmmonolt10-boldoblique.otf || kpsewhich lmmono10-italic.otf || kpsewhich lmmono10-regular.otf)
        cp \${REG} \${FONTDIR}/Inconsolatazi4-Regular.otf
        cp \${BOLD} \${FONTDIR}/Inconsolatazi4-Bold.otf
        cp \${IT} \${FONTDIR}/Inconsolatazi4-Italic.otf
        cp \${BOLDIT} \${FONTDIR}/Inconsolatazi4-BoldItalic.otf
        mktexlsr \${TEXDIR} 2>&1 | tail -2
        echo 'fontawesome/bbding/inconsolata/libertine/newtxmath/zlmtt stubs installed to' \${TEXDIR}/tex/latex/
    "
fi

# ── 5. 修补 gpt-academic latex_toolbox.py（fontset=windows → fandol on Linux）
echo "=== [5/10] 修补 latex_toolbox.py（fontset=windows → fandol on Linux）==="
docker exec -i -u root "$CONTAINER" python3 - << 'PYEOF'
import sys

path = '/gpt/crazy_functions/latex_fns/latex_toolbox.py'
with open(path, 'r') as f:
    src = f.read()

# 检查是否已修补
if '_fontset = "windows" if platform.system() == "Windows" else "fandol"' in src:
    print('  已修补，跳过')
    sys.exit(0)

start_marker = '        # fontset=windows\n'
end_marker = '        # find paper abstract'
start = src.find(start_marker)
end = src.find(end_marker, start)
if start < 0 or end < 0:
    print('  ERROR: 未找到 fontset=windows 代码块，请手动检查', file=sys.stderr)
    sys.exit(1)

new_block = ''.join([
    '        # fontset: use fandol on Linux (bundled with TeX Live), windows on Windows\n',
    '        import platform\n',
    '        _fontset = "windows" if platform.system() == "Windows" else "fandol"\n',
    '\n',
    '        main_file = re.sub(\n',
    '            r"\\\\documentclass\\[(.*?)\\]{(.*?)}",\n',
    '            r"\\\\documentclass[\\1,fontset=" + _fontset + r",UTF8]{\\2}",\n',
    '            main_file,\n',
    '        )\n',
    '        main_file = re.sub(\n',
    '            r"\\\\documentclass{(.*?)}",\n',
    '            r"\\\\documentclass[fontset=" + _fontset + r",UTF8]{\\1}",\n',
    '            main_file,\n',
    '        )\n',
])

src = src[:start] + new_block + src[end:]

with open(path, 'w') as f:
    f.write(src)

print('  已修补 latex_toolbox.py')
PYEOF

# ── 6. 修补 latex_actions.py（xelatex 检测列表加入 ctex）───────────────────
echo "=== [6/10] 修补 latex_actions.py（xelatex 检测加入 ctex）==="
ACTIONS=/gpt/crazy_functions/latex_fns/latex_actions.py
if docker exec "$CONTAINER" grep -q "'ctex'" "$ACTIONS"; then
    echo "  已修补，跳过"
else
    docker exec -u root "$CONTAINER" sed -i "s/'xunicode'\]/'xunicode', 'ctex']/g" "$ACTIONS"
    echo "  已修补 latex_actions.py"
fi

# ── 7. 修补 latex_toolbox.py：移除 xelatex 不兼容的 axessibility 包 ─────────
echo "=== [7/10] 修补 latex_toolbox.py（移除 axessibility pdflatex-only 包）==="
TOOLBOX=/gpt/crazy_functions/latex_fns/latex_toolbox.py
if docker exec "$CONTAINER" grep -q "axessibility" "$TOOLBOX"; then
    echo "  已修补，跳过"
else
    docker cp /root/workspace/paper-trans/scripts/patch_axessibility.py "$CONTAINER:/tmp/patch_axessibility.py"
    docker exec -u root "$CONTAINER" python3 /tmp/patch_axessibility.py
fi

# ── 8. 修补 latex_toolbox.py：find_main_tex_file 优先读 00README.json ─────────
echo "=== [8/10] 修补 latex_toolbox.py（find_main_tex_file：00README.json + 深度惩罚）==="
if docker exec "$CONTAINER" grep -q "00README.json" /gpt/crazy_functions/latex_fns/latex_toolbox.py; then
    echo "  已修补，跳过"
else
    docker cp /root/workspace/paper-trans/scripts/patch_find_main_tex.py "$CONTAINER:/tmp/patch_find_main_tex.py"
    docker exec -u root "$CONTAINER" python3 /tmp/patch_find_main_tex.py
fi

# ── 9. 修补 latex_toolbox.py：merge_tex_files_ 支持 \def\input@path ─────────
echo "=== [9/10] 修补 latex_toolbox.py（merge_tex_files_ 支持 \\def\\input@path）==="
TOOLBOX=/gpt/crazy_functions/latex_fns/latex_toolbox.py
if docker exec "$CONTAINER" grep -q "input_paths" "$TOOLBOX"; then
    echo "  已修补，跳过"
else
    docker exec -i -u root "$CONTAINER" python3 << 'PYPATCH'
import sys, re

path = '/gpt/crazy_functions/latex_fns/latex_toolbox.py'
with open(path, 'r') as f:
    lines = f.readlines()

# Find merge_tex_files_ function
idx = next((i for i, l in enumerate(lines) if 'def merge_tex_files_(project_foler' in l), None)
if idx is None:
    print('ERROR: merge_tex_files_ not found', file=sys.stderr)
    sys.exit(1)

# Find end of this top-level function without swallowing merge_tex_files().
end = next((i for i in range(idx + 1, len(lines)) if lines[i].startswith('def ')), len(lines))

# Check if already patched
existing = ''.join(lines[idx:end])
if 'input_paths' in existing:
    print('Already patched')
    sys.exit(0)

new_func = '''\
def merge_tex_files_(project_foler, main_file, mode, input_paths=None):
    """
    Merge Tex project recursively.
    Supports \\def\\input@path{{dir1/}{dir2/}...} for custom input directories.
    """
    main_file = rm_comments(main_file)
    if input_paths is None:
        input_paths = []
        for m in re.finditer(r"\\\\def\\\\input@path\\{(.*?)\\}", main_file):
            for d in re.finditer(r"\\{([^}]*/)\\}", m.group(1)):
                input_paths.append(d.group(1))
    for s in reversed([q for q in re.finditer(r"\\\\input\\{(.*?)\\}", main_file, re.M)]):
        f = s.group(1)
        fp = os.path.join(project_foler, f)
        fp_ = find_tex_file_ignore_case(fp)
        if fp_ is None:
            for prefix in input_paths:
                fp_alt = os.path.join(project_foler, prefix + f)
                fp_alt_ = find_tex_file_ignore_case(fp_alt)
                if fp_alt_:
                    fp_ = fp_alt_
                    break
        if fp_:
            try:
                with open(fp_, "r", encoding="utf-8", errors="replace") as fx:
                    c = fx.read()
            except:
                c = "\\n\\nWarning from GPT-Academic: LaTex source file is missing!\\n\\n"
        else:
            raise RuntimeError(f"找不到{fp}，Tex源文件缺失！")
        c = merge_tex_files_(project_foler, c, mode, input_paths=input_paths)
        main_file = main_file[: s.span()[0]] + c + main_file[s.span()[1] :]
    return main_file
'''

lines[idx:end] = [new_func]
with open(path, 'w') as f:
    f.writelines(lines)
print('Patched merge_tex_files_ with input@path support')
PYPATCH
fi

# ── 10. 修复 arxiv_cache 目录权限（防止 root 写入的文件导致 gptuser PermissionError）
echo "=== [10/10] 修复 arxiv_cache 目录权限 ==="
CACHE_DIR=$(docker exec "$CONTAINER" python3 -c "import sys; sys.path.insert(0,'/gpt'); import os; os.chdir('/gpt'); from toolbox import get_conf; print(get_conf('ARXIV_CACHE_DIR'))" 2>/dev/null || echo "gpt_log/arxiv_cache")
docker exec -u root "$CONTAINER" bash -c "
    if [ -d /gpt/${CACHE_DIR} ]; then
        chown -R gptuser:gptuser /gpt/${CACHE_DIR} 2>/dev/null || true
        echo '  arxiv_cache 权限已修复'
    else
        echo '  arxiv_cache 目录不存在，跳过'
    fi
"

echo ""
echo "=== setup_docker_env 完成 ==="
echo "验证："
docker exec "$CONTAINER" fc-match "SimSun"
docker exec "$CONTAINER" fc-match "Noto Sans SemiBold"
docker exec "$CONTAINER" kpsewhich bxcoloremoji.sty
docker exec "$CONTAINER" kpsewhich fontawesome.sty
docker exec "$CONTAINER" kpsewhich fontawesome5.sty
docker exec "$CONTAINER" kpsewhich fontawesome6.sty
docker exec "$CONTAINER" kpsewhich bbding.sty
docker exec "$CONTAINER" kpsewhich inconsolata.sty
docker exec "$CONTAINER" kpsewhich libertine.sty
docker exec "$CONTAINER" kpsewhich newtxmath.sty
docker exec "$CONTAINER" kpsewhich zlmtt.sty
docker exec "$CONTAINER" kpsewhich Inconsolatazi4-Regular.otf
docker exec "$CONTAINER" grep -q "fandol" /gpt/crazy_functions/latex_fns/latex_toolbox.py && echo "latex_toolbox.py fandol ✅"
docker exec "$CONTAINER" grep -q "ctex" /gpt/crazy_functions/latex_fns/latex_actions.py && echo "latex_actions.py ctex ✅"
docker exec "$CONTAINER" grep -q "axessibility" /gpt/crazy_functions/latex_fns/latex_toolbox.py && echo "latex_toolbox.py axessibility ✅"
docker exec "$CONTAINER" grep -q "00README.json" /gpt/crazy_functions/latex_fns/latex_toolbox.py && echo "latex_toolbox.py 00README ✅"
docker exec "$CONTAINER" grep -q "input_paths" /gpt/crazy_functions/latex_fns/latex_toolbox.py && echo "latex_toolbox.py input@path ✅"

docker exec -u root "$CONTAINER" bash -c "apt-get clean && rm -rf /var/lib/apt/lists/* /var/cache/apt/*"
