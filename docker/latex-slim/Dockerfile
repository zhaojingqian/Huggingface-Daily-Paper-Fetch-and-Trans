FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        fontconfig \
        fonts-arphic-ukai \
        fonts-arphic-uming \
        fonts-noto-cjk \
        fonts-noto-extra \
        ghostscript \
        git \
        latex-cjk-all \
        make \
        python3 \
        python3-pip \
        texlive-base \
        texlive-bibtex-extra \
        texlive-fonts-recommended \
        texlive-lang-chinese \
        texlive-lang-cjk \
        texlive-lang-european \
        texlive-latex-base \
        texlive-latex-extra \
        texlive-latex-recommended \
        texlive-pictures \
        texlive-science \
        texlive-xetex \
        unzip \
        xz-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    TEXDIR="$(kpsewhich -var-value TEXMFLOCAL)"; \
    mkdir -p \
        "${TEXDIR}/tex/latex/bbding" \
        "${TEXDIR}/tex/latex/fontawesome" \
        "${TEXDIR}/tex/latex/fontawesome5" \
        "${TEXDIR}/tex/latex/fontawesome6" \
        "${TEXDIR}/tex/latex/inconsolata" \
        "${TEXDIR}/tex/latex/libertine" \
        "${TEXDIR}/tex/latex/newtx" \
        "${TEXDIR}/tex/latex/zlmtt" \
        "${TEXDIR}/fonts/opentype/public/inconsolatazi4"; \
    cd /tmp; \
    curl -fsSL 'https://mirrors.ctan.org/macros/latex/contrib/bxcoloremoji.zip' -o bxcoloremoji.zip; \
    unzip -q bxcoloremoji.zip; \
    mkdir -p "${TEXDIR}/tex/latex/bxcoloremoji"; \
    cp bxcoloremoji/bxcoloremoji.sty bxcoloremoji/bxcoloremoji-names.def "${TEXDIR}/tex/latex/bxcoloremoji/"; \
    printf '%s\n' \
        '\NeedsTeXFormat{LaTeX2e}' \
        '\ProvidesPackage{fontawesome5}[2024/01/01 fontawesome5 stub]' \
        '\providecommand{\faIcon}[1]{}' \
        '\providecommand{\faicon}[1]{}' \
        '\providecommand{\faGithub}{}' \
        '\providecommand{\faTwitter}{}' \
        '\providecommand{\faEnvelope}{}' \
        '\providecommand{\faHome}{}' \
        '\providecommand{\faExternalLinkAlt}{}' \
        '\providecommand{\faFilePdf}{}' \
        > "${TEXDIR}/tex/latex/fontawesome5/fontawesome5.sty"; \
    printf '%s\n' \
        '\NeedsTeXFormat{LaTeX2e}' \
        '\ProvidesPackage{fontawesome}[2024/01/01 fontawesome v4 stub]' \
        '\RequirePackage{fontawesome5}' \
        '\providecommand{\faIcon}[1]{}' \
        '\providecommand{\faicon}[1]{}' \
        '\providecommand{\faGithub}{}' \
        '\providecommand{\faTwitter}{}' \
        '\providecommand{\faEnvelope}{}' \
        '\providecommand{\faHome}{}' \
        '\providecommand{\faExternalLink}{}' \
        '\providecommand{\faFilePdfO}{}' \
        > "${TEXDIR}/tex/latex/fontawesome/fontawesome.sty"; \
    printf '%s\n' \
        '\NeedsTeXFormat{LaTeX2e}' \
        '\ProvidesPackage{fontawesome6}[2024/01/01 fontawesome6 stub]' \
        '\RequirePackage{fontawesome5}' \
        '\providecommand{\faIcon}[1]{}' \
        '\providecommand{\faicon}[1]{}' \
        > "${TEXDIR}/tex/latex/fontawesome6/fontawesome6.sty"; \
    printf '%s\n' \
        '\NeedsTeXFormat{LaTeX2e}' \
        '\ProvidesPackage{fontawesome6-generic}[2024/01/01 fontawesome6-generic stub]' \
        '\RequirePackage{fontawesome6}' \
        > "${TEXDIR}/tex/latex/fontawesome6/fontawesome6-generic.sty"; \
    printf '%s\n' \
        '\NeedsTeXFormat{LaTeX2e}' \
        '\ProvidesPackage{bbding}[2024/01/01 bbding stub]' \
        '\providecommand{\Checkmark}{}' \
        '\providecommand{\XSolidBrush}{}' \
        '\providecommand{\HandRight}{}' \
        '\providecommand{\FiveStar}{}' \
        '\providecommand{\Envelope}{}' \
        '\providecommand{\ScissorRight}{}' \
        '\providecommand{\PencilRight}{}' \
        > "${TEXDIR}/tex/latex/bbding/bbding.sty"; \
    printf '%s\n' \
        '\NeedsTeXFormat{LaTeX2e}' \
        '\ProvidesPackage{inconsolata}[2024/01/01 inconsolata stub]' \
        '\DeclareOption*{}' \
        '\ProcessOptions\relax' \
        '\RequirePackage{lmodern}' \
        '\renewcommand{\ttdefault}{lmtt}' \
        > "${TEXDIR}/tex/latex/inconsolata/inconsolata.sty"; \
    printf '%s\n' \
        '\NeedsTeXFormat{LaTeX2e}' \
        '\ProvidesPackage{libertine}[2024/01/01 libertine stub]' \
        '\DeclareOption*{}' \
        '\ProcessOptions\relax' \
        '\RequirePackage{lmodern}' \
        '\renewcommand{\rmdefault}{lmr}' \
        '\renewcommand{\sfdefault}{lmss}' \
        '\renewcommand{\ttdefault}{lmtt}' \
        > "${TEXDIR}/tex/latex/libertine/libertine.sty"; \
    printf '%s\n' \
        '\NeedsTeXFormat{LaTeX2e}' \
        '\ProvidesPackage{newtxmath}[2024/01/01 newtxmath stub]' \
        '\DeclareOption*{}' \
        '\ProcessOptions\relax' \
        '\RequirePackage{amsmath}' \
        '\RequirePackage{amssymb}' \
        '\RequirePackage{mathrsfs}' \
        '\RequirePackage{bm}' \
        > "${TEXDIR}/tex/latex/newtx/newtxmath.sty"; \
    printf '%s\n' \
        '\NeedsTeXFormat{LaTeX2e}' \
        '\ProvidesPackage{zlmtt}[2024/01/01 zlmtt stub]' \
        '\DeclareOption*{}' \
        '\ProcessOptions\relax' \
        '\RequirePackage{lmodern}' \
        '\renewcommand{\ttdefault}{lmtt}' \
        > "${TEXDIR}/tex/latex/zlmtt/zlmtt.sty"; \
    REG="$(kpsewhich lmmono10-regular.otf)"; \
    BOLD="$(kpsewhich lmmonolt10-bold.otf || kpsewhich lmmono10-regular.otf)"; \
    IT="$(kpsewhich lmmono10-italic.otf || kpsewhich lmmono10-regular.otf)"; \
    BOLDIT="$(kpsewhich lmmonolt10-boldoblique.otf || kpsewhich lmmono10-italic.otf || kpsewhich lmmono10-regular.otf)"; \
    cp "${REG}" "${TEXDIR}/fonts/opentype/public/inconsolatazi4/Inconsolatazi4-Regular.otf"; \
    cp "${BOLD}" "${TEXDIR}/fonts/opentype/public/inconsolatazi4/Inconsolatazi4-Bold.otf"; \
    cp "${IT}" "${TEXDIR}/fonts/opentype/public/inconsolatazi4/Inconsolatazi4-Italic.otf"; \
    cp "${BOLDIT}" "${TEXDIR}/fonts/opentype/public/inconsolatazi4/Inconsolatazi4-BoldItalic.otf"; \
    mktexlsr "${TEXDIR}"; \
    rm -rf /tmp/bxcoloremoji /tmp/bxcoloremoji.zip

RUN python3 -m pip install --break-system-packages --no-cache-dir \
        "https://public.agent-matrix.com/publish/gradio-3.32.15-py3-none-any.whl" \
        "httpx<=0.25.2" \
        "pymdown-extensions>=10.14" \
        "requests[socks]" \
        anthropic \
        arxiv \
        beautifulsoup4 \
        colorama \
        dashscope \
        fastapi==0.110 \
        gradio-client==0.8 \
        latex2mathml \
        loguru \
        Markdown \
        matplotlib \
        mdtex2html \
        numpy \
        openai \
        pandas \
        prompt_toolkit \
        protobuf==3.20 \
        pydantic==2.9.2 \
        pygments \
        pymupdf \
        pypdf2==2.12.1 \
        python-docx \
        python-markdown-math \
        rich \
        rjsmin \
        tiktoken \
        websocket-client \
        zhipuai==2.0.1

RUN useradd -m gptuser

COPY --chown=gptuser:gptuser gpt /gpt

RUN mkdir -p /gpt/gpt_log/arxiv_cache \
    && chown -R gptuser:gptuser /gpt

WORKDIR /gpt
USER gptuser

CMD ["bash", "-lc", "tail -f /dev/null"]
