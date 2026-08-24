"""Shared pure result accounting for fetch/repair workflows.

The workflow entrypoints deliberately keep their public result dictionaries,
but they must not each invent a different residual/merge implementation.
This module owns only deterministic counter and residual bookkeeping; it does
not read indexes, call providers, or perform file I/O.
"""

from __future__ import annotations

from typing import Iterable, Mapping, MutableMapping, Sequence


BASE_FIELDS = (
    "metadata_attempted",
    "metadata_succeeded",
    "metadata_failed",
    "summary_attempted",
    "summary_succeeded",
    "summary_failed",
    "pdf_attempted",
    "pdf_succeeded",
    "pdf_failed",
)
REFETCH_FIELDS = BASE_FIELDS + (
    "refetch_attempted",
    "refetch_succeeded",
    "refetch_failed",
    "summary_repaired",
)


def new_stats(
    fields: Sequence[str] = BASE_FIELDS,
    *,
    audited: bool = False,
    abort_reason: bool = False,
) -> dict:
    """Create the stable counter shape used by one workflow boundary."""
    result = {str(field): 0 for field in fields}
    result.update({"residual_failures": 0, "residual_ids": []})
    if audited:
        result["audited_ids"] = []
    if abort_reason:
        result["abort_reason"] = ""
    return result


def _residual_ids(value) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {str(item) for item in value if item}


def finish_stats(
    stats: Mapping[str, object],
    residual_ids: Iterable[object] = (),
) -> dict:
    """Return a finalized copy with deterministic residual identity/count."""
    result = dict(stats)
    ids = _residual_ids(result.get("residual_ids"))
    ids.update(str(item) for item in residual_ids if item)
    result["residual_ids"] = sorted(ids)
    result["residual_failures"] = len(ids)
    if "audited_ids" in result:
        result["audited_ids"] = sorted(_residual_ids(result["audited_ids"]))
    return result


def merge_stats(
    target: MutableMapping[str, object],
    source: Mapping[str, object],
) -> MutableMapping[str, object]:
    """Merge counters and residuals, honoring later persisted audits.

    A source result that explicitly audited an ID with no residual is
    authoritative for that ID. Older workflows without ``audited_ids`` keep
    the simpler union behavior automatically.
    """
    for field, value in source.items():
        if field in {"residual_ids", "residual_failures", "audited_ids", "abort_reason"}:
            continue
        if isinstance(value, (int, float)) and isinstance(target.get(field), (int, float)):
            target[field] = target.get(field, 0) + value
    existing = _residual_ids(target.get("residual_ids"))
    audited = _residual_ids(source.get("audited_ids"))
    if audited:
        existing.difference_update(audited - _residual_ids(source.get("residual_ids")))
    existing.update(_residual_ids(source.get("residual_ids")))
    target["residual_ids"] = sorted(existing)
    target["residual_failures"] = len(existing)
    if audited:
        all_audited = _residual_ids(target.get("audited_ids"))
        all_audited.update(audited)
        target["audited_ids"] = sorted(all_audited)
    if source.get("abort_reason"):
        target["abort_reason"] = source["abort_reason"]
    return target


def stats_line(stats: Mapping[str, object]) -> str:
    """Format the common counters without assuming a specific workflow."""
    line = (
        f"metadata={stats.get('metadata_succeeded', 0)}/{stats.get('metadata_attempted', 0)}"
        f"(失败={stats.get('metadata_failed', 0)}) "
        f"summary={stats.get('summary_succeeded', 0)}/{stats.get('summary_attempted', 0)}"
        f"(失败={stats.get('summary_failed', 0)}"
    )
    if "summary_repaired" in stats:
        line += f", 修复={stats.get('summary_repaired', 0)}"
    line += ") "
    line += (
        f"pdf={stats.get('pdf_succeeded', 0)}/{stats.get('pdf_attempted', 0)}"
        f"(失败={stats.get('pdf_failed', 0)})"
    )
    if "refetch_attempted" in stats:
        line += (
            f" refetch={stats.get('refetch_succeeded', 0)}/{stats.get('refetch_attempted', 0)}"
            f"(失败={stats.get('refetch_failed', 0)})"
        )
    return line + f" 残留={stats.get('residual_failures', 0)}"
