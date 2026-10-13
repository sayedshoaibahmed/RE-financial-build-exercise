"""Account -> function + nature mapping (PLAN 3.6).

Hard rules enforced here, mechanically:
  * only <company_dir>/mapping.yaml is ever opened (never mapping.proposed.yaml);
  * every entry needs a non-empty approved_by and approved_on, and must not carry the
    proposal-only keys (proposed / rationale / confidence)     -> UNAPPROVED_MAPPING
  * function / nature must be ids from the vocabulary          -> UNKNOWN_CATEGORY
  * an account with no entry blocks the run                    -> UNMAPPED_ACCOUNT
    (unmapped_accounts.yaml is written; nothing is ever mapped to "Other")
  * one label under two different paths                        -> AMBIGUOUS_ACCOUNT
This module never writes mapping.yaml or mapping.proposed.yaml.
"""
import datetime
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from .config import SECTION_KINDS

MAPPING_FILE = "mapping.yaml"
PROPOSAL_KEYS = ("proposed", "rationale", "confidence")
ENTRY_KEYS = {"account", "section", "function", "nature", "group", "expected_negative",
              "approved_by", "approved_on"}


class MappingError(Exception):
    def __init__(self, code, message, unmapped=None):
        self.code = code
        self.unmapped = unmapped or []
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class MappingEntry:
    account: str
    section: str
    function: str
    nature: str
    group: str
    expected_negative: bool
    approved_by: str
    approved_on: str


@dataclass(frozen=True)
class MappedRecord:
    record: object
    function: str
    nature: str
    group: str
    expected_negative: bool


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _entry(raw, i, vocab):
    if not isinstance(raw, dict):
        raise MappingError("INVALID_MAPPING", f"entry {i + 1} is not a mapping")
    acct = raw.get("account")
    if not isinstance(acct, str) or not acct.strip():
        raise MappingError("INVALID_MAPPING", f"entry {i + 1} has no 'account'")
    acct = acct.strip()
    where = f"account {acct!r}"

    bad = [k for k in PROPOSAL_KEYS if k in raw]
    if bad:
        raise MappingError("UNAPPROVED_MAPPING", f"{where} still contains proposal-only key(s) "
                           f"{', '.join(bad)}; a human must remove them when approving")
    unknown = sorted(set(raw) - ENTRY_KEYS)
    if unknown:
        raise MappingError("INVALID_MAPPING", f"{where}: unknown key(s) {', '.join(map(repr, unknown))}")
    if _blank(raw.get("approved_by")):
        raise MappingError("UNAPPROVED_MAPPING", f"{where} has no approved_by")
    on = raw.get("approved_on")
    if isinstance(on, (datetime.date, datetime.datetime)):
        on = on.date().isoformat() if isinstance(on, datetime.datetime) else on.isoformat()
    if _blank(on):
        raise MappingError("UNAPPROVED_MAPPING", f"{where} has no approved_on")
    by = str(raw["approved_by"]).strip()

    section = raw.get("section")
    if section not in SECTION_KINDS:
        raise MappingError("INVALID_MAPPING", f"{where}: section {section!r} is not one of "
                           f"{', '.join(SECTION_KINDS)}")
    fn, nat, group = raw.get("function"), raw.get("nature"), raw.get("group")
    if section == "opex":
        if _blank(fn) or _blank(nat):
            raise MappingError("INVALID_MAPPING", f"{where}: opex entries need both function and nature")
        if group is not None:
            raise MappingError("INVALID_MAPPING", f"{where}: 'group' applies to non-opex sections only")
        if fn not in vocab.functions:
            raise MappingError("UNKNOWN_CATEGORY", f"{where}: function {fn!r} is not in the vocabulary "
                               f"({', '.join(sorted(vocab.functions))})")
        if nat not in vocab.natures:
            raise MappingError("UNKNOWN_CATEGORY", f"{where}: nature {nat!r} is not in the vocabulary "
                               f"({', '.join(sorted(vocab.natures))})")
        group = None
    else:
        if fn is not None or nat is not None:
            raise MappingError("INVALID_MAPPING", f"{where}: function/nature apply to opex entries only")
        if group is not None and _blank(group):
            raise MappingError("INVALID_MAPPING", f"{where}: empty group")
        group = str(group).strip() if group is not None else acct
    neg = raw.get("expected_negative", False)
    if not isinstance(neg, bool):
        raise MappingError("INVALID_MAPPING", f"{where}: expected_negative must be true or false")
    return MappingEntry(acct, section, fn, nat, group, neg, by, str(on).strip())


def load_mapping(company_dir, vocab):
    """-> {account: MappingEntry}. A missing mapping.yaml means nothing is mapped yet."""
    f = Path(company_dir) / MAPPING_FILE
    if not f.is_file():
        return {}
    try:
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise MappingError("INVALID_MAPPING", f"{f.name} is not valid YAML: {e}") from e
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise MappingError("INVALID_MAPPING", f"{f.name} must be a list of entries")
    out = {}
    for i, r in enumerate(raw):
        e = _entry(r, i, vocab)
        if e.account in out:
            raise MappingError("INVALID_MAPPING", f"account {e.account!r} appears more than once")
        out[e.account] = e
    return out


def _accounts(records):
    """Distinct (account, section, path) in first-appearance (sheet) order."""
    seen = {}
    for r in records:
        seen.setdefault((r.account, r.section, tuple(r.account_path)), None)
    return list(seen)


def mapping_status(records, mapping):
    return [{"account": a, "section": s, "path": list(p), "mapped": a in mapping}
            for a, s, p in _accounts(records)]


def _unmapped_doc(records, missing):
    rows = []
    for a, s, p in missing:
        mine = sorted((r for r in records if r.account == a and tuple(r.account_path) == p),
                      key=lambda r: r.period)
        nz = [r.period for r in mine if r.amount != 0]
        rows.append({"account": a, "section": s, "path": list(p),
                     "first_nonzero_period": nz[0] if nz else None,
                     "last_nonzero_period": nz[-1] if nz else None,
                     "total": str(sum((r.amount for r in mine), Decimal("0.00")))})
    return rows


def map_records(records, mapping, company, company_dir, out_dir=None):
    """-> list[MappedRecord], or raises MappingError (AMBIGUOUS_ACCOUNT, UNMAPPED_ACCOUNT, INVALID_MAPPING)."""
    accts = _accounts(records)
    paths = {}
    for a, s, p in accts:
        paths.setdefault(a, []).append(p)
    amb = {a: ps for a, ps in paths.items() if len(ps) > 1}
    if amb:
        detail = "; ".join(f"{a!r} appears under " + " and ".join("[" + " > ".join(p) + "]" for p in ps)
                           for a, ps in amb.items())
        raise MappingError("AMBIGUOUS_ACCOUNT", "the same account label appears under different paths, so "
                           f"it cannot be mapped by label: {detail}")

    missing = [(a, s, p) for a, s, p in accts if a not in mapping]
    if missing:
        out = Path(out_dir) if out_dir else (Path(company_dir).resolve().parent.parent / "outputs"
                                             / company.company_id / "unmapped")
        out.mkdir(parents=True, exist_ok=True)
        doc = _unmapped_doc(records, missing)
        (out / "unmapped_accounts.yaml").write_text(
            "# Accounts with no entry in mapping.yaml. A human must approve a mapping for each.\n"
            + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        names = ", ".join(repr(a) for a, _, _ in missing[:10]) + (" ..." if len(missing) > 10 else "")
        raise MappingError("UNMAPPED_ACCOUNT", f"{len(missing)} account(s) have no entry in mapping.yaml: "
                           f"{names}. See {out / 'unmapped_accounts.yaml'}", unmapped=doc)

    for a, s, _ in accts:
        if mapping[a].section != s:
            raise MappingError("INVALID_MAPPING", f"account {a!r} is mapped with section "
                               f"{mapping[a].section!r} but the workbook has it in {s!r}")
    return [MappedRecord(r, mapping[r.account].function, mapping[r.account].nature,
                         mapping[r.account].group, mapping[r.account].expected_negative)
            for r in records]


def category_totals(mapped, field):
    """{period: {category: Decimal}} over opex records. `field` is 'function' or 'nature'.
    A record with no category is left out, so reconciliation C9 exposes it."""
    if field not in ("function", "nature"):
        raise ValueError("field must be 'function' or 'nature'")
    out = {}
    for m in mapped:
        d = out.setdefault(m.record.period, {})
        cat = getattr(m, field)
        if m.record.section != "opex" or cat is None:
            continue
        d[cat] = d.get(cat, Decimal("0.00")) + m.record.amount
    return out
