"""Load a game adapter file and turn it into prompts and output schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ADAPTER_DIR = Path(__file__).resolve().parent.parent / "adapters"

PARSERS = {"text", "int", "number", "datetime", "sign", "ratio_first", "ratio_second"}
MAKES = {"fill", "order_snapshot", "balance_snapshot"}


class AdapterError(ValueError):
    pass


@dataclass
class Column:
    name: str
    header: str
    hint: str = ""


@dataclass
class Observation:
    name: str
    values: list[str]
    hint: str = ""


@dataclass
class Screen:
    name: str
    makes: str
    looks_like: str
    columns: list[Column]
    fields: dict[str, dict[str, Any]]
    skip_columns: list[str] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    sections: dict[str, str] | None = None
    derive: dict[str, str] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    row_key: list[str] = field(default_factory=list)
    order_key: list[str] = field(default_factory=list)

    def raw_row_schema(self) -> dict[str, Any]:
        props: dict[str, Any] = {c.name: {"type": "string"} for c in self.columns}
        for o in self.observations:
            props[o.name] = {"type": "string", "enum": list(o.values)}
        props["partial"] = {"type": "boolean"}
        return {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        }

    def raw_schema(self) -> dict[str, Any]:
        section = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "rows": {"type": "array", "items": self.raw_row_schema()},
            },
            "required": ["name", "rows"],
            "additionalProperties": False,
        }
        if self.sections:
            section["properties"]["name"] = {"type": "string", "enum": list(self.sections)}
        return {
            "type": "object",
            "properties": {"sections": {"type": "array", "items": section}},
            "required": ["sections"],
            "additionalProperties": False,
        }

    def instructions(self) -> str:
        lines = [f"Screen: {self.looks_like}", "", "For each table row, copy these cells:"]
        for c in self.columns:
            extra = f" ({c.hint})" if c.hint else ""
            lines.append(f'- "{c.name}": the "{c.header}" column{extra}')
        for o in self.observations:
            extra = f" ({o.hint})" if o.hint else ""
            lines.append(f'- "{o.name}": one of {", ".join(o.values)}{extra}')
        lines.append('- "partial": true only if the row is cut off by the top or bottom edge of the image')
        if self.skip_columns:
            lines.append(f"Do not copy these columns: {', '.join(self.skip_columns)}.")
        if self.sections:
            lines.append(
                "Return one entry in \"sections\" per section shown, named exactly "
                + " or ".join(f'"{s}"' for s in self.sections)
                + ", in the order they appear."
            )
        else:
            lines.append('Return a single entry in "sections" with "name" set to "".')
        return "\n".join(lines)


@dataclass
class Adapter:
    game: str
    name: str
    currency: str
    screens: dict[str, Screen]
    fees: list[dict[str, Any]]

    def screen(self, name: str) -> Screen:
        if name not in self.screens:
            raise AdapterError(f"{self.game} has no screen named {name!r}; known: {', '.join(self.screens)}")
        return self.screens[name]

    def classify_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"screen": {"type": "string", "enum": [*self.screens, "unknown"]}},
            "required": ["screen"],
            "additionalProperties": False,
        }

    def classify_instructions(self) -> str:
        lines = [f"Which {self.name} screen is this screenshot? The known screens are:"]
        for s in self.screens.values():
            lines.append(f'- "{s.name}": {s.looks_like}')
        lines.append('Answer "unknown" if it is none of them.')
        return "\n".join(lines)


def _screen(name: str, raw: dict[str, Any]) -> Screen:
    try:
        columns = [Column(**c) for c in raw["columns"]]
        screen = Screen(
            name=name,
            makes=raw["makes"],
            looks_like=" ".join(str(raw["looks_like"]).split()),
            columns=columns,
            fields=raw["fields"],
            skip_columns=raw.get("skip_columns", []),
            observations=[Observation(**o) for o in raw.get("observations", [])],
            sections=raw.get("sections"),
            derive=raw.get("derive", {}),
            checks=raw.get("checks", []),
            row_key=raw.get("row_key", []),
            order_key=raw.get("order_key", []),
        )
    except (KeyError, TypeError) as e:
        raise AdapterError(f"screen {name!r}: {e}") from e

    if screen.makes not in MAKES:
        raise AdapterError(f"screen {name!r}: makes must be one of {sorted(MAKES)}")
    names = {c.name for c in columns} | {o.name for o in screen.observations}
    for fname, spec in screen.fields.items():
        if spec.get("from") not in names:
            raise AdapterError(f"screen {name!r}: field {fname!r} reads unknown column {spec.get('from')!r}")
        if spec.get("parse", "text") not in PARSERS:
            raise AdapterError(f"screen {name!r}: field {fname!r} has unknown parse {spec.get('parse')!r}")
    return screen


def load_adapter(game: str, directory: Path = ADAPTER_DIR) -> Adapter:
    path = directory / f"{game}.yaml"
    if not path.exists():
        raise AdapterError(f"no adapter file at {path}")
    raw = yaml.safe_load(path.read_text())
    screens = {n: _screen(n, s) for n, s in raw["screens"].items()}
    return Adapter(
        game=raw["game"],
        name=raw["name"],
        currency=raw.get("currency", ""),
        screens=screens,
        fees=raw.get("fees", []),
    )
