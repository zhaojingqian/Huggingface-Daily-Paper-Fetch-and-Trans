#!/usr/bin/env bash
# setup_docker_env.sh — 修复 gpt-academic-latex 容器的 LaTeX 编译环境
#
# 解决问题：
#   1. Noto Sans SemiBold 缺失（2603.16859 类论文使用 mac_automl 模板）
#   2. fontset=windows 注入 Windows 专有 CJK 字体（SimSun/SimHei/KaiTi）在 Linux 不存在
#   3. bxcoloremoji 包缺失（部分论文使用 emoji）
#   4. ctex 包不在 xelatex 检测列表导致使用 pdflatex 编译失败
#   5. \def\input@path 自定义路径无法被 gpt-academic 解析，导致多级目录论文 merge 失败
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

CONTAINER="gpt-academic-latex"

echo "=== [setup_docker_env] 检查容器 $CONTAINER 是否运行 ==="
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "❌ 容器 $CONTAINER 未运行，请先启动容器"
    exit 1
fi

# ── 1. 安装字体包 ────────────────────────────────────────────────────────────
echo "=== [1/8] 安装 Noto CJK + Arphic + Noto Extra 字体 ==="
docker exec -u root "$CONTAINER" apt-get update -qq
docker exec -u root "$CONTAINER" apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    fonts-noto-extra \
    fonts-arphic-ukai \
    fonts-arphic-uming

# ── 2. 写入 fontconfig 映射（Windows CJK 字体名 → Linux 等效字体）────────────
echo "=== [2/8] 写入 fontconfig 映射规则 ==="
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
echo "=== [3/8] 安装 bxcoloremoji LaTeX 包 ==="
ALREADY=$(docker exec "$CONTAINER" kpsewhich bxcoloremoji.sty 2>/dev/null)
if [ -n "$ALREADY" ]; then
    echo "  bxcoloremoji 已安装，跳过"
else
    docker exec -u root "$CONTAINER" bash -c "
        cd /tmp && \
        curl -fsSL 'https://mirrors.ctan.org/macros/latex/contrib/bxcoloremoji.zip' -o bxcoloremoji.zip && \
        unzip -q bxcoloremoji.zip && \
        TEXDIR=\$(kpsewhich -var-value TEXMFLOCAL) && \
        mkdir -p \${TEXDIR}/tex/latex/bxcoloremoji && \
        cp bxcoloremoji/bxcoloremoji.sty bxcoloremoji/bxcoloremoji-names.def \${TEXDIR}/tex/latex/bxcoloremoji/ && \
        mktexlsr \${TEXDIR} 2>&1 | tail -2 && \
        echo 'bxcoloremoji installed to' \${TEXDIR}/tex/latex/bxcoloremoji/
    "
fi

# ── 4. 修补 gpt-academic latex_toolbox.py（fontset=windows → fandol on Linux）
echo "=== [4/8] 修补 latex_toolbox.py（fontset=windows → fandol on Linux）==="
docker exec -u root "$CONTAINER" python3 - << 'PYEOF'
import sys

path = '/gpt/crazy_functions/latex_fns/latex_toolbox.py'
with open(path, 'r') as f:
    lines = f.readlines()

# 检查是否已修补
if any('fandol' in l for l in lines):
    print('  已修补，跳过')
    sys.exit(0)

# 找到 "# fontset=windows" 注释行
target = '        # fontset=windows\n'
idx = next((i for i, l in enumerate(lines) if l == target), None)
if idx is None:
    print('  ERROR: 未找到 fontset=windows 代码块，请手动检查', file=sys.stderr)
    sys.exit(1)

# 验证后续 13 行符合预期（第 idx 到 idx+12 共 13 行）
expected_slice = [
    '        # fontset=windows\n',
    '        import platform\n',
    '\n',
    '        main_file = re.sub(\n',
    '            r"\\\\documentclass\\[(.*?)\\]{(.*?)}",\n',
    '            r"\\\\documentclass[\\1,fontset=windows,UTF8]{\\2}",\n',
    '            main_file,\n',
    '        )\n',
    '        main_file = re.sub(\n',
    '            r"\\\\documentclass{(.*?)}",\n',
    '            r"\\\\documentclass[fontset=windows,UTF8]{\\1}",\n',
    '            main_file,\n',
    '        )\n',
]
if lines[idx:idx+13] != expected_slice:
    print('  ERROR: 代码块内容与预期不符，跳过自动修补', file=sys.stderr)
    sys.exit(1)

new_lines = [
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
]

lines[idx:idx+13] = new_lines

with open(path, 'w') as f:
    f.writelines(lines)

print('  已修补 latex_toolbox.py')
PYEOF

# ── 5. 修补 latex_actions.py（xelatex 检测列表加入 ctex）───────────────────
echo "=== [5/8] 修补 latex_actions.py（xelatex 检测加入 ctex）==="
ACTIONS=/gpt/crazy_functions/latex_fns/latex_actions.py
if docker exec "$CONTAINER" grep -q "'ctex'" "$ACTIONS"; then
    echo "  已修补，跳过"
else
    docker exec -u root "$CONTAINER" sed -i "s/'xunicode'\]/'xunicode', 'ctex']/g" "$ACTIONS"
    echo "  已修补 latex_actions.py"
fi

# ── 6. 修补 latex_toolbox.py：移除 xelatex 不兼容的 axessibility 包 ─────────
echo "=== [6/8] 修补 latex_toolbox.py（移除 axessibility pdflatex-only 包）==="
TOOLBOX=/gpt/crazy_functions/latex_fns/latex_toolbox.py
if docker exec "$CONTAINER" grep -q "axessibility" "$TOOLBOX"; then
    echo "  已修补，跳过"
else
    docker cp /root/workspace/paper-trans/scripts/patch_axessibility.py "$CONTAINER:/tmp/patch_axessibility.py"
    docker exec -u root "$CONTAINER" python3 /tmp/patch_axessibility.py
fi

# ── 7. 修补 latex_toolbox.py：merge_tex_files_ 支持 \def\input@path ─────────
echo "=== [7/8] 修补 latex_toolbox.py（merge_tex_files_ 支持 \\def\\input@path）==="
TOOLBOX=/gpt/crazy_functions/latex_fns/latex_toolbox.py
if docker exec "$CONTAINER" grep -q "input_paths" "$TOOLBOX"; then
    echo "  已修补，跳过"
else
    docker exec -u root "$CONTAINER" python3 << 'PYPATCH'
import sys, re

path = '/gpt/crazy_functions/latex_fns/latex_toolbox.py'
with open(path, 'r') as f:
    lines = f.readlines()

# Find merge_tex_files_ function
idx = next((i for i, l in enumerate(lines) if 'def merge_tex_files_(project_foler' in l), None)
if idx is None:
    print('ERROR: merge_tex_files_ not found', file=sys.stderr)
    sys.exit(1)

# Find end of function (next blank line after function body)
end = idx + 1
while end < len(lines) and (lines[end].strip() or end < idx + 5):
    if end > idx + 5 and lines[end] == '\n' and end + 1 < len(lines) and lines[end+1] == '\n':
        break
    end += 1
# Make sure we include the return statement
while end < len(lines) and 'return main_file' not in lines[end]:
    end += 1
end += 1  # include return line

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

# ── 7. 修复 arxiv_cache 目录权限（防止 root 写入的文件导致 gptuser PermissionError）
echo "=== [8/8] 修复 arxiv_cache 目录权限 ==="
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
docker exec "$CONTAINER" grep -c "fandol" /gpt/crazy_functions/latex_fns/latex_toolbox.py && echo "latex_toolbox.py fandol ✅"
docker exec "$CONTAINER" grep -c "ctex" /gpt/crazy_functions/latex_fns/latex_actions.py && echo "latex_actions.py ctex ✅"
docker exec "$CONTAINER" grep -c "axessibility" /gpt/crazy_functions/latex_fns/latex_toolbox.py && echo "latex_toolbox.py axessibility ✅"
docker exec "$CONTAINER" grep -c "input_paths" /gpt/crazy_functions/latex_fns/latex_toolbox.py && echo "latex_toolbox.py input@path ✅"
