"""One owner for arXiv source archives and translation workfolder recovery.

The translation driver still owns paper lifecycle and retry decisions.  This
module owns only the disposable source-cache boundary: bounded download,
archive validation, compile-cache cleanup, and reconstruction of a workfolder
when a translated TeX file survived but the extracted source did not.
"""

from __future__ import annotations

import glob
import os
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

try:
    from translation_policy import bounded_int as _bounded_policy_int
except ImportError:
    from paperhub.translation_policy import bounded_int as _bounded_policy_int


@dataclass(frozen=True)
class SourceDownloadPolicy:
    """Bound each source-download attempt and the complete retry sequence."""

    connect_timeout: int = 15
    read_timeout: int = 90
    attempt_seconds: int = 300
    total_seconds: int = 600
    rate_grace_seconds: int = 45
    min_bytes_per_second: int = 8 * 1024
    max_bytes: int = 512 * 1024 * 1024

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "SourceDownloadPolicy":
        values = os.environ if env is None else env
        return cls(
            connect_timeout=_bounded_policy_int(
                values.get("PAPER_TRANS_SOURCE_CONNECT_TIMEOUT"),
                15,
                minimum=5,
                maximum=60,
            ),
            read_timeout=_bounded_policy_int(
                values.get("PAPER_TRANS_SOURCE_READ_TIMEOUT"),
                90,
                minimum=15,
                maximum=300,
            ),
            attempt_seconds=_bounded_policy_int(
                values.get("PAPER_TRANS_SOURCE_ATTEMPT_SECONDS"),
                300,
                minimum=60,
                maximum=1800,
            ),
            total_seconds=_bounded_policy_int(
                values.get("PAPER_TRANS_SOURCE_TOTAL_SECONDS"),
                600,
                minimum=120,
                maximum=3600,
            ),
            rate_grace_seconds=_bounded_policy_int(
                values.get("PAPER_TRANS_SOURCE_RATE_GRACE_SECONDS"),
                45,
                minimum=10,
                maximum=300,
            ),
            min_bytes_per_second=_bounded_policy_int(
                values.get("PAPER_TRANS_SOURCE_MIN_BYTES_PER_SECOND"),
                8 * 1024,
                minimum=1024,
                maximum=512 * 1024,
            ),
            max_bytes=_bounded_policy_int(
                values.get("PAPER_TRANS_SOURCE_MAX_BYTES"),
                512 * 1024 * 1024,
                minimum=1024 * 1024,
                maximum=2 * 1024 * 1024 * 1024,
            ),
        )


@dataclass
class SourceCache:
    """Manage one paper's source archive and disposable extracted state."""

    cache_dir: str
    paper_id: str
    proxies: Mapping[str, str]
    session_factory: Callable[[], object]
    safety_error: Callable[[str], Optional[str]]
    policy: SourceDownloadPolicy

    @property
    def paper_dir(self) -> str:
        return os.path.join(self.cache_dir, self.paper_id)

    @property
    def source_dir(self) -> str:
        return os.path.join(self.paper_dir, "e-print")

    @property
    def source_tar(self) -> str:
        return os.path.join(self.source_dir, self.paper_id + ".tar")

    @property
    def extract_dir(self) -> str:
        return os.path.join(self.paper_dir, "extract")

    def clear_compile_cache(self, full: bool = False) -> None:
        """Clear disposable compile output, optionally including extraction."""
        targets = ["workfolder", "translation"]
        if full:
            targets.append("extract")
        for subdir in targets:
            directory = os.path.join(self.paper_dir, subdir)
            if os.path.exists(directory):
                shutil.rmtree(directory)
                print(f"[driver] 已清除缓存: {directory}", flush=True)

    def is_valid(self) -> bool:
        """Return whether the cached source is large enough and archive-safe."""
        if not os.path.exists(self.source_tar) or os.path.getsize(self.source_tar) < 1024:
            return False
        reason = self.safety_error(self.source_tar)
        if reason:
            print(f"[driver] ⚠️  arXiv 源码缓存不安全/无效: {reason}", flush=True)
            return False
        return True

    def _content_length(self, response) -> Optional[int]:
        """Return a bounded Content-Length when the server supplied one."""
        raw = response.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise RuntimeError(f"invalid Content-Length: {raw!r}")
        if value < 0:
            raise RuntimeError(f"invalid Content-Length: {raw!r}")
        if value > self.policy.max_bytes:
            raise RuntimeError(
                "source archive exceeds download limit: "
                f"{value} > {self.policy.max_bytes} bytes"
            )
        return value

    def prefetch(self, max_rounds: int = 3) -> bool:
        """Download source through proxy/direct fallbacks with hard time bounds."""
        if self.is_valid():
            print(f"[driver] ♻️  arXiv 源码缓存已存在: {self.source_tar}", flush=True)
            return True

        os.makedirs(self.source_dir, exist_ok=True)
        temporary = self.source_tar + ".part"
        url = f"https://arxiv.org/e-print/{self.paper_id}"
        plans = [("proxy", True), ("direct", False)]
        total_deadline = time.monotonic() + self.policy.total_seconds

        for round_idx in range(1, max_rounds + 1):
            for label, use_proxy in plans:
                try:
                    remaining = total_deadline - time.monotonic()
                    if remaining <= 0:
                        print(
                            "[driver] ⚠️  arXiv 源码预下载达到总时限 "
                            f"({self.policy.total_seconds}s)，停止重试",
                            flush=True,
                        )
                        return False
                    attempt_started = time.monotonic()
                    attempt_deadline = min(
                        total_deadline,
                        attempt_started + self.policy.attempt_seconds,
                    )
                    if os.path.exists(temporary):
                        os.remove(temporary)
                    session = self.session_factory()
                    if use_proxy:
                        session.proxies.update(self.proxies)
                    else:
                        session.trust_env = False
                    print(
                        "[driver] ⬇️  预下载 arXiv 源码 "
                        f"({label}, round={round_idx}): {url}",
                        flush=True,
                    )
                    request_read_timeout = min(
                        self.policy.read_timeout,
                        max(15, int(remaining)),
                    )
                    with session.get(
                        url,
                        stream=True,
                        timeout=(self.policy.connect_timeout, request_read_timeout),
                    ) as response:
                        response.raise_for_status()
                        content_length = self._content_length(response)
                        if response.headers.get("Content-Encoding", "").lower() not in (
                            "",
                            "identity",
                        ):
                            content_length = None
                        written = 0
                        with open(temporary, "wb") as handle:
                            for chunk in response.iter_content(chunk_size=1024 * 256):
                                if chunk:
                                    handle.write(chunk)
                                    written += len(chunk)
                                elapsed = time.monotonic() - attempt_started
                                if time.monotonic() > attempt_deadline:
                                    raise TimeoutError(
                                        "source download attempt exceeded "
                                        f"{int(attempt_deadline - attempt_started)}s"
                                    )
                                if (
                                    elapsed >= self.policy.rate_grace_seconds
                                    and written / max(elapsed, 1)
                                    < self.policy.min_bytes_per_second
                                ):
                                    raise TimeoutError(
                                        "source download below minimum rate: "
                                        f"{written / max(elapsed, 1):.0f}B/s < "
                                        f"{self.policy.min_bytes_per_second}B/s"
                                    )
                        if content_length is not None and written != content_length:
                            raise RuntimeError(
                                "incomplete source archive: "
                                f"expected {content_length} bytes, got {written}"
                            )
                    if os.path.getsize(temporary) < 1024:
                        raise RuntimeError("downloaded source is too small")
                    unsafe_reason = self.safety_error(temporary)
                    if unsafe_reason:
                        raise RuntimeError(
                            "downloaded source archive rejected: " + unsafe_reason
                        )
                    os.replace(temporary, self.source_tar)
                    kb = os.path.getsize(self.source_tar) // 1024
                    print(
                        f"[driver] ✅ arXiv 源码预下载成功: {self.source_tar} ({kb}KB)",
                        flush=True,
                    )
                    return True
                except Exception as exc:
                    print(
                        "[driver] ⚠️  arXiv 源码预下载失败 "
                        f"({label}, round={round_idx}): {type(exc).__name__}: {exc}",
                        flush=True,
                    )
                    try:
                        if os.path.exists(temporary):
                            os.remove(temporary)
                    except Exception:
                        pass
            remaining = total_deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(2 * round_idx, 6, remaining))
        return False

    def restore_workfolder(self, translated_tex_path: str) -> bool:
        """Rebuild extracted source state while preserving translated TeX."""
        if not os.path.exists(translated_tex_path):
            return False
        try:
            with open(translated_tex_path, "rb") as handle:
                translated_tex = handle.read()
        except Exception as exc:
            print(
                f"[driver] ⚠️  读取翻译 tex 失败，无法恢复 workfolder: {exc}",
                flush=True,
            )
            return False

        if not (self.is_valid() or self.prefetch()):
            print("[driver] ⚠️  源码缓存不可用，无法恢复 workfolder", flush=True)
            return False

        try:
            from toolbox import extract_archive
            from crazy_functions.Latex_Function import (
                descend_to_extracted_folder_if_exist,
                move_project,
            )
            from crazy_functions.latex_fns import latex_toolbox as latex_tools

            if os.path.exists(self.extract_dir):
                shutil.rmtree(self.extract_dir)
            os.makedirs(self.extract_dir, exist_ok=True)
            extract_archive(file_path=self.source_tar, dest_dir=self.extract_dir)

            project_folder = descend_to_extracted_folder_if_exist(self.extract_dir)
            os.makedirs(project_folder, exist_ok=True)
            with open(os.path.join(project_folder, "merge_translate_zh.tex"), "wb") as handle:
                handle.write(translated_tex)

            workfolder = move_project(project_folder, self.paper_id)
            with open(os.path.join(workfolder, "merge_translate_zh.tex"), "wb") as handle:
                handle.write(translated_tex)

            tex_files = [
                path
                for path in glob.glob(f"{workfolder}/**/*.tex", recursive=True)
                if not os.path.basename(path).startswith("merge")
            ]
            if tex_files:
                maintex = latex_tools.find_main_tex_file(tex_files, "translate_zh")
                with open(maintex, encoding="utf-8", errors="replace") as handle:
                    merged_content = latex_tools.merge_tex_files(
                        workfolder,
                        handle.read(),
                        "translate_zh",
                    )
                with open(
                    os.path.join(workfolder, "merge.tex"),
                    "w",
                    encoding="utf-8",
                    errors="replace",
                ) as handle:
                    handle.write(merged_content)
                print(
                    f"[driver] ✅ 已恢复完整 workfolder 并生成 merge.tex: {workfolder}",
                    flush=True,
                )
            else:
                print(
                    "[driver] ⚠️  源码解压后未找到 tex 文件，仅恢复中文 tex: "
                    f"{workfolder}",
                    flush=True,
                )
            return True
        except Exception as exc:
            print(
                "[driver] ⚠️  恢复 keep-translation workfolder 失败: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            return False
