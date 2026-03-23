#!/usr/bin/env python3
"""
Patch latex_toolbox.py to remove axessibility package when using xelatex.

The axessibility package with the accsupp option uses pdflatex-only primitives
(e.g. pdfglyphtounicode) that XeTeX/XeLaTeX does not have. This causes
'Undefined control sequence' errors aborting compilation with 100 errors.

Inserts a re.sub call inside merge_tex_files to strip axessibility lines
from the merged tex file before xelatex compilation.
"""
import sys
import re

path = '/gpt/crazy_functions/latex_fns/latex_toolbox.py'
with open(path, 'r') as f:
    src = f.read()

if 'axessibility' in src:
    print('  Already patched, skipping')
    sys.exit(0)

# Find "# find paper abstract" comment inside merge_tex_files
# This is where we insert the axessibility removal block
marker = '        # find paper abstract'
idx = src.find(marker)
if idx < 0:
    print('  ERROR: anchor "# find paper abstract" not found', file=sys.stderr)
    sys.exit(1)

# Check that _fontset variable exists above this point (fandol patch must be applied first)
fontset_section = src[:idx]
if '_fontset' not in fontset_section:
    print('  ERROR: _fontset not found above marker - apply fandol patch first', file=sys.stderr)
    sys.exit(1)

insertion = (
    '        # On Linux/xelatex, remove packages that use pdflatex-only primitives.\n'
    '        # axessibility with accsupp uses \\pdfglyphtounicode which XeTeX lacks.\n'
    '        if _fontset != "windows":\n'
    '            main_file = re.sub(\n'
    '                r"\\\\usepackage\\[accsupp\\]\\{axessibility\\}[^\\n]*\\n?",\n'
    '                "% axessibility removed (xelatex incompatible)\\n",\n'
    '                main_file,\n'
    '            )\n'
    '            main_file = re.sub(\n'
    '                r"\\\\usepackage\\{axessibility\\}[^\\n]*\\n?",\n'
    '                "% axessibility removed (xelatex incompatible)\\n",\n'
    '                main_file,\n'
    '            )\n'
)

src = src[:idx] + insertion + src[idx:]
with open(path, 'w') as f:
    f.write(src)
print('  Patched: axessibility removal added to merge_tex_files')
