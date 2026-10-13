"""Load and validate company.yaml. Unknown keys are rejected, never ignored.

P1 knows only the `source:` block (PLAN 3.2). Later phases add keys here.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SECTION_KINDS = ("income", "cogs", "opex", "other_income", "other_expense")
TOP_LEVEL_KEYS = {"source"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class SectionCfg:
    header: str
    total: str
    kind: str


@dataclass(frozen=True)
class GroupCfg:
    header: str
    total: str


@dataclass(frozen=True)
class SourceConfig:
    type: str
    path: Path
    sheet: str
    header_row: int
    section_label_col: str
    account_label_col: str
    period_header_format: str
    sections: tuple
    groups: tuple
    # Convention (PLAN 3.2 lists names only): [Gross Profit, Operating Income, Net Income], in that order.
    derived_rows: tuple


@dataclass(frozen=True)
class Company:
    company_id: str
    source: SourceConfig


def _mapping(v, where):
    if not isinstance(v, dict):
        raise ConfigError(f"{where}: expected a mapping, got {type(v).__name__}")
    return v


def _check_keys(d, allowed, required, where):
    unknown = sorted(set(d) - set(allowed))
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(map(repr, unknown))}; "
                          f"allowed: {', '.join(sorted(allowed))}")
    missing = [k for k in required if k not in d]
    if missing:
        raise ConfigError(f"{where}: missing required key(s) {', '.join(map(repr, missing))}")


def _str(d, key, where):
    v = d[key]
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"{where}.{key}: expected a non-empty string")
    return v.strip()


def _col(d, key, where):
    v = _str(d, key, where)
    if not re.fullmatch(r"[A-Za-z]{1,3}", v):
        raise ConfigError(f"{where}.{key}: expected a column letter like 'A', got {v!r}")
    return v.upper()


def _col_index(letter):
    n = 0
    for ch in letter:
        n = n * 26 + ord(ch) - 64
    return n


def _list_of_maps(d, key, where, allowed, required):
    v = d.get(key, [])
    if not isinstance(v, list):
        raise ConfigError(f"{where}.{key}: expected a list")
    for i, item in enumerate(v):
        _check_keys(_mapping(item, f"{where}.{key}[{i}]"), allowed, required, f"{where}.{key}[{i}]")
    return v


def parse_source(raw, base_dir):
    w = "source"
    raw = _mapping(raw, w)
    _check_keys(raw, {"type", "path", "sheet", "header_row", "section_label_col", "account_label_col",
                      "period_header_format", "sections", "groups", "derived_rows"},
                {"type", "path", "sheet", "header_row", "sections", "derived_rows"}, w)
    if _str(raw, "type", w) != "excel_pl":
        raise ConfigError(f"{w}.type: only 'excel_pl' is supported here, got {raw['type']!r}")
    hr = raw["header_row"]
    if isinstance(hr, bool) or not isinstance(hr, int) or hr < 1:
        raise ConfigError(f"{w}.header_row: expected a positive integer")
    sec_col = _col({"c": raw.get("section_label_col", "A")}, "c", w + ".section_label_col")
    acc_col = _col({"c": raw.get("account_label_col", "B")}, "c", w + ".account_label_col")
    if sec_col == acc_col:
        raise ConfigError(f"{w}: section_label_col and account_label_col must differ")
    fmt = raw.get("period_header_format", "%b %Y")
    if not isinstance(fmt, str) or not fmt.strip():
        raise ConfigError(f"{w}.period_header_format: expected a non-empty string")

    secs = _list_of_maps(raw, "sections", w, {"header", "total", "kind"}, {"header", "total", "kind"})
    if not secs:
        raise ConfigError(f"{w}.sections: at least one section is required")
    sections = []
    for i, s in enumerate(secs):
        where = f"{w}.sections[{i}]"
        kind = _str(s, "kind", where)
        if kind not in SECTION_KINDS:
            raise ConfigError(f"{where}.kind: {kind!r} is not one of {', '.join(SECTION_KINDS)}")
        sections.append(SectionCfg(_str(s, "header", where), _str(s, "total", where), kind))
    grps = _list_of_maps(raw, "groups", w, {"header", "total"}, {"header", "total"})
    groups = [GroupCfg(_str(g, "header", f"{w}.groups[{i}]"), _str(g, "total", f"{w}.groups[{i}]"))
              for i, g in enumerate(grps)]
    der = raw["derived_rows"]
    if not isinstance(der, list) or len(der) != 3 or not all(isinstance(x, str) and x.strip() for x in der):
        raise ConfigError(f"{w}.derived_rows: expected exactly 3 names, in order "
                          "[Gross Profit, Operating Income, Net Income]")
    derived = tuple(x.strip() for x in der)

    labels = ([s.header for s in sections] + [s.total for s in sections] +
              [g.header for g in groups] + [g.total for g in groups] + list(derived))
    dup = sorted({x for x in labels if labels.count(x) > 1})
    if dup:
        raise ConfigError(f"{w}: label(s) declared more than once: {', '.join(map(repr, dup))}")

    p = Path(_str(raw, "path", w))
    if not p.is_absolute():
        p = Path(base_dir) / p
    return SourceConfig("excel_pl", p, _str(raw, "sheet", w), hr, sec_col, acc_col, fmt,
                        tuple(sections), tuple(groups), derived)


def load_company(company_dir, base_dir=None):
    """company_id = folder name. Relative source paths resolve against base_dir
    (default: the folder that contains `companies/`)."""
    company_dir = Path(company_dir).resolve()
    f = company_dir / "company.yaml"
    if not f.is_file():
        raise ConfigError(f"company.yaml not found in {company_dir}")
    try:
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{f}: invalid YAML: {e}") from e
    raw = _mapping(raw, str(f))
    _check_keys(raw, TOP_LEVEL_KEYS, {"source"}, "company.yaml")
    base = Path(base_dir) if base_dir else company_dir.parent.parent
    return Company(company_dir.name, parse_source(raw["source"], base))
