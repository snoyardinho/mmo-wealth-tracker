"""Turn raw transcribed cells into shared records, using the adapter's field rules.

The model only copies text. Every number, date and side is decided here, in code.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .adapter import Screen


class ParseError(ValueError):
    pass


_NUMBER_JUNK = re.compile(r"[^\d.\-]")


def parse_number(text: str) -> Decimal:
    """'-1,234,567.50 ISK' -> Decimal('-1234567.50'). Thousands separators are commas."""
    cleaned = text.replace("−", "-").replace(",", "")
    cleaned = _NUMBER_JUNK.sub("", cleaned)
    if cleaned in ("", "-", ".", "-."):
        raise ParseError(f"no number in {text!r}")
    try:
        return Decimal(cleaned)
    except InvalidOperation as e:
        raise ParseError(f"bad number {text!r}") from e


def parse_int(text: str) -> int:
    value = parse_number(text)
    if value != value.to_integral_value():
        raise ParseError(f"expected a whole number, got {text!r}")
    return int(value)


def parse_ratio(text: str, part: int) -> int:
    pieces = text.split("/")
    if len(pieces) != 2:
        raise ParseError(f"expected 'a/b', got {text!r}")
    return parse_int(pieces[part])


def parse_field(spec: dict[str, Any], cell: str) -> Any:
    kind = spec.get("parse", "text")
    cell = cell.strip()
    if kind == "text":
        # Game fonts and models disagree on dash characters; store a plain hyphen.
        cell = cell.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
        return " ".join(cell.split())
    if kind == "int":
        return parse_int(cell)
    if kind == "number":
        value = parse_number(cell)
        return abs(value) if spec.get("absolute") else value
    if kind == "datetime":
        try:
            return datetime.strptime(cell, spec["format"])
        except ValueError as e:
            raise ParseError(f"bad date {cell!r} for format {spec['format']!r}") from e
    if kind == "sign":
        value = parse_number(cell)
        return spec["negative"] if value < 0 else spec["positive"]
    if kind == "ratio_first":
        return parse_ratio(cell, 0)
    if kind == "ratio_second":
        return parse_ratio(cell, 1)
    raise ParseError(f"unknown parse kind {kind!r}")


def _derive(expr: str, record: dict[str, Any]) -> Any:
    """Supports 'a / b' and 'a * b' between two record fields."""
    m = re.fullmatch(r"\s*(\w+)\s*([/*])\s*(\w+)\s*", expr)
    if not m:
        raise ParseError(f"unsupported derive expression {expr!r}")
    a, op, b = record[m.group(1)], m.group(2), record[m.group(3)]
    a, b = Decimal(a), Decimal(b)
    return a / b if op == "/" else a * b


def normalize(screen: Screen, raw: dict[str, Any], source: str = "") -> dict[str, Any]:
    """Returns {"records": [...], "partial": [...], "errors": [...]}.

    Each record keeps its source screenshot, section and row number for review.
    """
    records: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for section in raw.get("sections", []):
        section_name = section.get("name", "")
        for i, row in enumerate(section.get("rows", [])):
            where = {"source": source, "section": section_name, "row": i}
            if row.get("partial"):
                partial.append({**where, "raw": row})
                continue
            record: dict[str, Any] = {"kind": screen.makes}
            try:
                for fname, spec in screen.fields.items():
                    record[fname] = parse_field(spec, row[spec["from"]])
                if screen.sections:
                    if section_name not in screen.sections:
                        raise ParseError(f"unknown section {section_name!r}")
                    record["side"] = screen.sections[section_name]
                for fname, expr in screen.derive.items():
                    record[fname] = _derive(expr, record)
            except (ParseError, KeyError, ZeroDivisionError) as e:
                errors.append({**where, "raw": row, "error": str(e)})
                continue
            for o in screen.observations:
                record[f"_{o.name}"] = row.get(o.name)
            record["_where"] = where
            records.append(record)
    return {"records": records, "partial": partial, "errors": errors}
