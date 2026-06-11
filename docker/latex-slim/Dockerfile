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
        texlive-fonts-extra \
        texlive-fonts-recommended \
        texlive-lang-chinese \
        texlive-lang-cjk \
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
        mdtex2html \
        numpy \
        openai \
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
