"""Command line: extract one screenshot, or run the golden test over a game's samples.

    python -m tracker extract samples/eve/history-01.png --game eve
    python -m tracker golden --game eve
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .adapter import ADAPTER_DIR, Adapter, load_adapter
from .checks import run_checks
from .compare import as_rows, compare
from .normalize import normalize
from .stitch import key_for, stitch

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "raw"


def _cache_key(adapter_game: str, image: Path, model: str, effort: str) -> str:
    h = hashlib.sha256()
    h.update(image.read_bytes())
    h.update((ADAPTER_DIR / f"{adapter_game}.yaml").read_bytes())
    h.update(f"{model}|{effort}".encode())
    return h.hexdigest()[:32]


def read_screenshot(adapter: Adapter, image: Path, screen_name: str | None, *, model: str, effort: str,
                    offline: bool, extractor_box: list) -> tuple[str, dict[str, Any]]:
    """Returns (screen name, raw cells), using the cache so a screenshot is never paid for twice."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{_cache_key(adapter.game, image, model, effort)}.json"
    if cached.exists():
        data = json.loads(cached.read_text())
        return data["screen"], data["raw"]
    if offline:
        raise SystemExit(f"{image.name}: not in the cache and --offline was given")
    if not extractor_box:
        from .extract import Extractor  # imported late so offline runs need no API key
        extractor_box.append(Extractor(adapter, model=model, effort=effort))
    extractor = extractor_box[0]
    screen = adapter.screen(screen_name) if screen_name else None
    screen, raw = extractor.extract(image, screen)
    cached.write_text(json.dumps({"screen": screen.name, "raw": raw}, indent=1))
    return screen.name, raw


def _expected_screen(answer: Path) -> str | None:
    for line in answer.read_text().splitlines():
        if line.startswith("# screen:"):
            return line.split(":", 1)[1].strip()
    return None


def cmd_extract(args: argparse.Namespace) -> int:
    adapter = load_adapter(args.game)
    image = Path(args.image)
    screen_name, raw = read_screenshot(adapter, image, args.screen, model=args.model, effort=args.effort,
                                       offline=args.offline, extractor_box=[])
    screen = adapter.screen(screen_name)
    result = normalize(screen, raw, source=image.name)
    problems = run_checks(screen, result["records"])
    columns = [f for f in screen.fields if f != "shown_unit_price"] + list(screen.derive)
    if screen.sections:
        columns.insert(0, "side")
    print(f"{image.name}: {screen_name}, {len(result['records'])} rows, "
          f"{len(result['partial'])} cut off, {len(result['errors'])} unreadable, {len(problems)} flagged")
    print(",".join(columns))
    for row in as_rows(result["records"], columns):
        print(",".join(f'"{c}"' if "," in c else c for c in row))
    for e in result["errors"]:
        print(f"UNREADABLE row {e['row']}: {e['error']}", file=sys.stderr)
    for p in problems:
        print(f"FLAG {p['check']} row {p['row']['row'] if p['row'] else '?'}: {p['detail']}", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(json.dumps(raw, indent=1))
    return 0


def cmd_golden(args: argparse.Namespace) -> int:
    adapter = load_adapter(args.game)
    sample_dir = ROOT / "samples" / args.game
    answers = sorted(sample_dir.glob("*.expected.csv"))
    if not answers:
        raise SystemExit(f"no answer files in {sample_dir}")
    box: list = []
    lines: list[str] = [f"# Golden run: {adapter.name}", "", f"Model {args.model}, effort {args.effort}.", ""]
    all_passed = True
    history_pages: list[list[dict[str, Any]]] = []
    for answer in answers:
        image = answer.with_name(answer.name.replace(".expected.csv", ".png"))
        want_screen = _expected_screen(answer)
        screen_name, raw = read_screenshot(adapter, image, None, model=args.model, effort=args.effort,
                                           offline=args.offline, extractor_box=box)
        screen = adapter.screen(screen_name)
        result = normalize(screen, raw, source=image.name)
        problems = run_checks(screen, result["records"])
        report = compare(result["records"], answer)
        classified_ok = want_screen is None or want_screen == screen_name
        passed = report["passed"] and classified_ok and not result["errors"]
        all_passed &= passed
        if screen.makes == "fill":
            history_pages.append(result["records"])
        lines.append(f"## {image.name}: {'PASS' if passed else 'FAIL'}")
        lines.append(f"- screen: {screen_name}" + ("" if classified_ok else f" (expected {want_screen})"))
        lines.append(f"- rows: {report['correct']} of {report['expected']} correct, {report['extracted']} extracted, "
                     f"{len(result['partial'])} cut-off rows set aside, {len(result['errors'])} unreadable")
        lines.append(f"- flagged by checks: {len(problems)}")
        for w in report["wrong"]:
            diffs = "; ".join(f"{c}: expected {e!r}, got {g!r}" for c, (e, g) in w["fields"].items())
            lines.append(f"  - row {w['expected_row']}: {diffs}")
        for m in report["missing"]:
            lines.append(f"  - missing: {', '.join(m)}")
        for x in report["extra"]:
            lines.append(f"  - extra: {', '.join(x)}")
        for p in problems:
            lines.append(f"  - flag {p['check']} row {p['row']['row'] if p['row'] else '?'}: {p['detail']}")
        lines.append("")

    if history_pages:
        screen = next(s for s in adapter.screens.values() if s.makes == "fill")
        joined = stitch(history_pages, key_for(screen.row_key))
        lines.append("## Stitched history")
        lines.append(f"- {sum(map(len, history_pages))} rows on pages, {len(joined['rows'])} after joining")
        for j in joined["joins"]:
            note = "no shared rows, a gap is possible" if j["gap_possible"] else f"{j['overlap']} shared rows"
            if j.get("ambiguous"):
                note += ", identical rows at the seam: confirm in review"
            lines.append(f"- page {j['page'] + 1}: {note}")
        lines.append("")

    if box:
        stats = box[0].stats
        cost = stats.cost_usd(args.model)
        lines.append("## Cost and time (new API calls only)")
        lines.append(f"- {stats.calls} calls, {stats.input_tokens:,} input and {stats.output_tokens:,} output tokens")
        if cost is not None:
            lines.append(f"- about ${cost:.3f} in total, ${cost / max(1, len(answers)):.3f} per screenshot")
        lines.append(f"- {stats.seconds:.0f} s in total, {stats.seconds / max(1, len(answers)):.0f} s per screenshot")
        lines.append(f"- served by: {', '.join(sorted(set(stats.models)))}")
    else:
        lines.append("All screenshots came from the cache; no API calls were made.")
    lines.append("")
    lines.append(f"**Result: {'PASS' if all_passed else 'FAIL'}**")
    text = "\n".join(lines)
    print(text)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text + "\n")
    return 0 if all_passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--game", default="eve")
        p.add_argument("--model", default="claude-opus-5-5")
        p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
        p.add_argument("--offline", action="store_true", help="use cached results only; never call the API")

    p = sub.add_parser("extract", help="read one screenshot and print its rows")
    p.add_argument("image")
    p.add_argument("--screen", help="skip classification and use this screen")
    p.add_argument("--out", help="save the raw cells as JSON")
    common(p)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("golden", help="check every sample against its answer file")
    p.add_argument("--report", help="also write the report to this file")
    common(p)
    p.set_defaults(func=cmd_golden)

    args = parser.parse_args(argv)
    return args.func(args)
