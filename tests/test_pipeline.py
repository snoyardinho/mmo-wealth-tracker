"""Offline tests: everything after the model's transcription, using the answer files as input.

A perfect transcription is rebuilt from each answer file in the game's on-screen format,
then run through normalize, checks, compare and stitch. No API calls.
"""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracker.adapter import load_adapter
from tracker.checks import run_checks
from tracker.compare import compare
from tracker.extract import Extractor
from tracker.normalize import ParseError, normalize, parse_number
from tracker.stitch import key_for, stitch

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "eve"
ADAPTER = load_adapter("eve")


def _answers(name: str) -> list[dict[str, str]]:
    lines = [l for l in (SAMPLES / name).read_text().splitlines() if l and not l.startswith("#")]
    return list(csv.DictReader(lines))


def _isk(value: Decimal, sign: str = "") -> str:
    """Format like the game: 1,234,567 or 1,234.50, with ISK."""
    text = f"{value:,.2f}" if value != value.to_integral_value() else f"{int(value):,}"
    return f"{sign}{text} ISK"


def fake_transactions_raw(name: str) -> dict:
    rows = []
    for a in _answers(name):
        qty, total = int(a["quantity"]), Decimal(a["total"])
        shown = (total / qty).quantize(Decimal(1))
        rows.append({
            "date": datetime.strptime(a["time"], "%Y-%m-%d %H:%M").strftime("%Y.%m.%d %H:%M"),
            "quantity": f"{qty:,}",
            "type": a["item"],
            "unit_price": _isk(shown),
            "total": _isk(total, "-" if a["side"] == "buy" else ""),
            "where": a["location"].replace(" - ", " – "),  # the model may return an en dash
            "total_color": "red" if a["side"] == "buy" else "green",
            "partial": False,
        })
    return {"sections": [{"name": "", "rows": rows}]}


def fake_orders_raw(name: str) -> dict:
    sections = {"Selling": [], "Buying": []}
    for a in _answers(name):
        section = "Selling" if a["side"] == "sell" else "Buying"
        sections[section].append({
            "type": a["item"],
            "quantity": f"{int(a['remaining_quantity']):,}/{int(a['original_quantity']):,}",
            "price": _isk(Decimal(a["price"]).quantize(Decimal("0.01"))),
            "station": a["location"],
            "expires_in": "84d 14h 24m 4s",
            "partial": False,
        })
    return {"sections": [{"name": n, "rows": r} for n, r in sections.items()]}


HISTORY = [f"history-0{i}.expected.csv" for i in range(1, 7)]


@pytest.mark.parametrize("name", HISTORY)
def test_history_pages_round_trip(name):
    screen = ADAPTER.screen("transactions")
    result = normalize(screen, fake_transactions_raw(name), source=name)
    assert result["errors"] == []
    assert run_checks(screen, result["records"]) == []
    report = compare(result["records"], SAMPLES / name)
    assert report["passed"], report


def test_orders_round_trip():
    screen = ADAPTER.screen("orders")
    result = normalize(screen, fake_orders_raw("orders-01.expected.csv"))
    assert result["errors"] == []
    report = compare(result["records"], SAMPLES / "orders-01.expected.csv")
    assert report["passed"], report
    nova = next(r for r in result["records"] if r["item"] == "Nova Precision Heavy Missile")
    assert (nova["side"], nova["remaining_quantity"], nova["original_quantity"]) == ("sell", 4359, 24970)


def test_unit_price_comes_from_total_not_the_rounded_screen_value():
    screen = ADAPTER.screen("transactions")
    records = normalize(screen, fake_transactions_raw("history-01.expected.csv"))["records"]
    row = records[2]  # 1,280 Nova Precision Heavy Missile for 527,488 ISK, shown as 412
    assert row["shown_unit_price"] == 412
    assert row["unit_price"] == Decimal(527488) / 1280  # 412.1


def test_checks_flag_a_misread_total_and_a_wrong_colour():
    screen = ADAPTER.screen("transactions")
    raw = fake_transactions_raw("history-01.expected.csv")
    raw["sections"][0]["rows"][0]["total"] = "4,121,000 ISK"   # extra digit
    raw["sections"][0]["rows"][1]["total_color"] = "red"         # sale shown as red
    problems = run_checks(screen, normalize(screen, raw)["records"])
    assert {(p["check"], p["row"]["row"]) for p in problems} == {
        ("shown_unit_price_close", 0),
        ("side_matches_colour", 1),
    }


def test_compare_reports_the_wrong_field():
    screen = ADAPTER.screen("transactions")
    raw = fake_transactions_raw("history-06.expected.csv")
    raw["sections"][0]["rows"][3]["quantity"] = "41"
    report = compare(normalize(screen, raw)["records"], SAMPLES / "history-06.expected.csv")
    assert not report["passed"]
    assert report["wrong"] == [{"expected_row": 4, "fields": {"quantity": ("4", "41")}}]


def test_cut_off_rows_are_set_aside():
    screen = ADAPTER.screen("transactions")
    raw = fake_transactions_raw("history-04.expected.csv")
    raw["sections"][0]["rows"].append({**raw["sections"][0]["rows"][-1], "partial": True})
    result = normalize(screen, raw)
    assert len(result["partial"]) == 1
    assert compare(result["records"], SAMPLES / "history-04.expected.csv")["passed"]


def test_stitching_the_six_history_pages():
    screen = ADAPTER.screen("transactions")
    pages = [normalize(screen, fake_transactions_raw(n))["records"] for n in HISTORY]
    shuffled = [pages[i] for i in (3, 0, 5, 1, 4, 2)]  # upload order must not matter
    joined = stitch(shuffled, key_for(screen.row_key))
    overlaps = [(j["overlap"], j["gap_possible"]) for j in joined["joins"]]
    # 1->2 share one row; 2->3 share two; 3->4 and 4->5 share none (the 04:14 row is cut off
    # on page 4, so a gap is real there); 5->6 share one row.
    assert overlaps == [(1, False), (2, False), (0, True), (0, True), (1, False)]
    assert len(joined["rows"]) == sum(map(len, pages)) - 4


def test_identical_real_fills_are_both_kept():
    screen = ADAPTER.screen("transactions")
    page = normalize(screen, fake_transactions_raw("history-04.expected.csv"))["records"]
    joined = stitch([page, page[8:]], key_for(screen.row_key))  # second page repeats from row 9
    twins = [r for r in joined["rows"] if r["time"] == datetime(2026, 11, 17, 11, 36) and r["quantity"] == 8]
    assert len(twins) == 2


def test_stitch_flags_identical_rows_at_the_seam():
    rows = [{"k": "a"}, {"k": "b"}, {"k": "b"}]
    joined = stitch([rows, [{"k": "b"}, {"k": "c"}]], key_for(["k"]))
    assert joined["joins"][0]["ambiguous"] is False
    joined = stitch([rows, [{"k": "b"}, {"k": "b"}, {"k": "c"}]], key_for(["k"]))
    assert joined["joins"][0] == {"page": 1, "overlap": 2, "gap_possible": False, "ambiguous": True}


@pytest.mark.parametrize("text,value", [
    ("-2,854,612 ISK", Decimal(-2854612)),
    ("412.10 ISK", Decimal("412.10")),
    ("−53,592 ISK", Decimal(-53592)),
    ("1,483,000", Decimal(1483000)),
])
def test_parse_number(text, value):
    assert parse_number(text) == value


def test_parse_number_rejects_empty():
    with pytest.raises(ParseError):
        parse_number("ISK")


def test_schemas_are_strict_and_list_every_column():
    for screen in ADAPTER.screens.values():
        row = screen.raw_row_schema()
        assert row["additionalProperties"] is False
        assert set(row["required"]) == set(row["properties"])
        cells = screen.instructions().split("copy these cells:")[1].split("Do not copy")[0]
        assert "Client" not in cells and "Region" not in cells
    assert ADAPTER.classify_schema()["properties"]["screen"]["enum"] == ["transactions", "orders", "unknown"]


def test_extractor_reads_the_json_reply():
    """The API call itself is stubbed: this checks request shape and reply handling."""
    sent = {}

    class Stream:
        def __init__(self, **kwargs):
            sent.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return SimpleNamespace(
                stop_reason="end_turn", model="claude-opus-5-5",
                usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
                content=[SimpleNamespace(type="text", text='{"sections": []}')],
            )

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=Stream)))
    extractor = Extractor(ADAPTER, client=client)
    screen, raw = extractor.extract(SAMPLES / "history-06.png", ADAPTER.screen("transactions"))
    assert raw == {"sections": []}
    assert sent["output_config"]["format"]["schema"] == screen.raw_schema()
    assert sent["messages"][0]["content"][0]["source"]["media_type"] == "image/png"
    assert extractor.stats.cost_usd() == pytest.approx((1000 * 4 + 200 * 20) / 1e6)
