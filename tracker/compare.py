"""Compare extracted records with a hand-checked answer file (CSV)."""

from __future__ import annotations

import csv
import difflib
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

TIME_FORMAT = "%Y-%m-%d %H:%M"


def _cell(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime(TIME_FORMAT)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def load_answers(path: Path) -> tuple[list[str], list[tuple[str, ...]]]:
    with path.open(newline="") as f:
        reader = csv.reader(row for row in f if row.strip() and not row.startswith("#"))
        header = next(reader)
        rows = [tuple(_canon(c, v) for c, v in zip(header, row)) for row in reader]
    return header, rows


def _canon(column: str, value: str) -> str:
    """Write numbers in answer files with or without commas; compare them the same way."""
    value = value.strip()
    if column in {"quantity", "total", "price", "remaining_quantity", "original_quantity"}:
        return _cell(Decimal(value.replace(",", "")))
    return value


def as_rows(records: list[dict[str, Any]], header: list[str]) -> list[tuple[str, ...]]:
    return [tuple(_cell(r.get(c)) for c in header) for r in records]


def compare(records: list[dict[str, Any]], answer_file: Path) -> dict[str, Any]:
    header, expected = load_answers(answer_file)
    got = as_rows(records, header)
    matcher = difflib.SequenceMatcher(a=expected, b=got, autojunk=False)
    wrong: list[dict[str, Any]] = []
    missing: list[tuple[str, ...]] = []
    extra: list[tuple[str, ...]] = []
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            continue
        exp, act = expected[a0:a1], got[b0:b1]
        for offset, (e, g) in enumerate(zip(exp, act)):  # same position: report which fields differ
            fields = {c: (ev, gv) for c, ev, gv in zip(header, e, g) if ev != gv}
            wrong.append({"expected_row": a0 + offset + 1, "fields": fields})
        missing.extend(exp[len(act):])
        extra.extend(act[len(exp):])
    correct = sum(size for _, _, size in matcher.get_matching_blocks())
    return {
        "header": header,
        "expected": len(expected),
        "extracted": len(got),
        "correct": correct,
        "wrong": wrong,
        "missing": missing,
        "extra": extra,
        "passed": correct == len(expected) == len(got),
    }
