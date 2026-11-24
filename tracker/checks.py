"""Row checks driven by the adapter. A failed check flags a row for review; it never edits it."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .adapter import Screen


def run_checks(screen: Screen, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns a list of {"row": where, "check": name, "detail": text}."""
    problems: list[dict[str, Any]] = []
    for check in screen.checks:
        name = check["name"]
        if name == "side_matches_colour":
            obs = f"_{check['observation']}"
            for r in records:
                want = check["expect"].get(r.get("side"))
                got = r.get(obs)
                if want and got != want:
                    problems.append(_p(r, name, f"side {r.get('side')} but colour {got}"))
        elif name == "shown_unit_price_close":
            tol = Decimal(str(check.get("tolerance_per_unit", 1)))
            for r in records:
                shown, total, qty = r.get("shown_unit_price"), r.get("total"), r.get("quantity")
                if shown is None or total is None or not qty:
                    continue
                if abs(shown * qty - total) > tol * qty:
                    problems.append(_p(r, name, f"{shown} x {qty} = {shown * qty}, total says {total}"))
        elif name == "time_not_increasing":
            for prev, cur in zip(records, records[1:]):
                if cur["_where"]["section"] == prev["_where"]["section"] and cur["time"] > prev["time"]:
                    problems.append(_p(cur, name, f"{cur['time']} is later than the row above ({prev['time']})"))
        else:
            problems.append({"row": None, "check": name, "detail": "unknown check in adapter"})
    return problems


def _p(record: dict[str, Any], check: str, detail: str) -> dict[str, Any]:
    return {"row": record["_where"], "check": check, "detail": detail}
