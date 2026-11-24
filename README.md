# MMO wealth tracker

Tracks market trading in MMOs from screenshots, so you can see which items earn their keep
and whether your wealth is growing. One pipeline serves every game; each game only needs
an adapter file.

## How it works

1. **Screenshot** a market screen in the game (trade history or open orders).
2. **Classify:** a Claude vision model says which screen of which game it is.
3. **Extract:** the model copies every visible row, cell by cell, into a fixed JSON schema
   generated from the game's adapter file. It is told never to calculate or correct anything.
4. **Normalize:** plain code turns the copied text into records: numbers, dates, buy or sell,
   and derived values such as unit price.
5. **Check:** rules from the adapter flag suspicious rows (colour does not match the sign,
   shown price × quantity does not match the total, times out of order).
6. **Stitch:** overlapping history pages are joined without dropping real identical fills.

Milestone 1 covers steps 1 to 6 as a command-line tool. Review screens, the ledger and the
per-game page come in later milestones.

## Setup

```sh
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=...   # needed only for new screenshots
```

## Commands

```sh
# Read one screenshot and print its rows
python -m tracker extract samples/eve/history-01.png --game eve

# Check every sample against its hand-checked answer file, with cost and time
python -m tracker golden --game eve --report reports/golden-eve.md

# Re-run the checks on cached results only, with no API calls
python -m tracker golden --game eve --offline

# Offline unit tests
python -m pytest
```

Every model reply is cached in `.cache/raw/`, keyed by the image, the adapter file, the
model and the effort level, so a screenshot is never paid for twice.

## Adding a game

1. Write `adapters/<game>.yaml`, using `adapters/eve.yaml` as the pattern: the screens, their
   columns, how each field is parsed, the checks, and the fee rules.
2. Put sample screenshots in `samples/<game>/`, each with a hand-checked
   `<name>.expected.csv` answer file. The first line `# screen: <name>` says which screen it is.
3. Run `python -m tracker golden --game <game>` until it passes.

If a new game needs a change outside its adapter file, that change belongs in the shared
code, so every later game benefits.

## EVE notes

- The unit price on screen is rounded to whole ISK; the total is exact. Unit price is
  therefore total ÷ quantity.
- Buy or sell comes from the sign of the total and is cross-checked against its colour.
- The Client column holds other players' names and is never read or stored.
- An order is identified by item, side, station and original quantity, because the orders
  screen shows no order id. A new price on the same order means one modify.

## Status

- [x] EVE adapter, extraction, normalization, checks, stitching, golden runner
- [x] Answer files for 7 EVE samples (228 rows), still to be confirmed by hand
- [ ] First golden run against the Claude API (needs an API key)
