"""Join overlapping history pages into one list without dropping real identical rows.

Pages are lists of rows, newest first, as the game shows them. Two consecutive pages
usually share a few rows. We find the longest run where the end of what we have
equals the start of the next page, and append only what comes after it. Two real
identical fills inside a page are both kept because matching is by position in a
sequence, not by set membership.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

Key = Callable[[dict[str, Any]], tuple]


def key_for(fields: Sequence[str]) -> Key:
    return lambda r: tuple(r.get(f) for f in fields)


def overlaps(tail: list[tuple], head: list[tuple]) -> list[int]:
    """Every k > 0 with tail[-k:] == head[:k], longest first."""
    return [k for k in range(min(len(tail), len(head)), 0, -1) if tail[-k:] == head[:k]]


def stitch(pages: list[list[dict[str, Any]]], key: Key) -> dict[str, Any]:
    """Returns {"rows": [...], "joins": [{"page": i, "overlap": k, "gap_possible": bool}]}.

    Pages are sorted newest first by their first row's time when they carry one,
    so upload order does not matter.
    """
    pages = [p for p in pages if p]
    if all("time" in p[0] for p in pages):
        pages = sorted(pages, key=lambda p: p[0]["time"], reverse=True)
    rows: list[dict[str, Any]] = []
    joins: list[dict[str, Any]] = []
    for i, page in enumerate(pages):
        keys = [key(r) for r in page]
        found = overlaps([key(r) for r in rows], keys) if rows else []
        k = found[0] if found else 0
        if rows:
            # More than one possible overlap means identical rows sit at the seam,
            # so a person should confirm the join in review.
            joins.append({"page": i, "overlap": k, "gap_possible": k == 0, "ambiguous": len(found) > 1})
        rows.extend(page[k:])
    return {"rows": rows, "joins": joins}
