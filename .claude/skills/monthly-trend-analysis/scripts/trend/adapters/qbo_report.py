"""QuickBooks Online ProfitAndLoss report JSON -> flat table (PLAN 3.3). One report per month.

Input is a folder of .json files (or a single file); the month comes from each report's Header, not the file name. Each file is the connector's envelope
{"_notice", "data"} or a bare report. Classification is strict, like the Excel adapter: anything that
matches no rule is a hard error, never a guess.

  * top-level Section with a `group` in the alias table -> a section; its Data rows and nested Sections are records
  * top-level Section whose group is a derived row (GrossProfit, NetIncome, ...) -> controls only
  * nested Section -> a path element; a non-empty Header amount is the parent's OWN balance (a record)
  * every Summary amount -> a control, named by its label (a label used twice in one file is path-qualified)
  * a blank amount on a Data row or Summary is NON_NUMERIC, never zero

The same leaf label under different parents is accepted here. (mapping.py keys accounts by label and
rejects it as AMBIGUOUS_ACCOUNT; that is a mapping-stage rule, not an adapter one.)
"""
import json
import re
from calendar import monthrange
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..model import ControlTotal, Period, Record, money
from .base import AdapterError

_TOTAL = re.compile(r"^total\b", re.IGNORECASE)
_AMOUNT = re.compile(r"^-?\d+(\.\d{1,2})?$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


@dataclass(frozen=True)
class SectionAlias:
    kind: str            # income | cogs | opex | other_income | other_expense
    header: str = None   # account_path[0]; default = the report's own section label
    total: str = None    # control name for the section's Summary; default = the report's own label


DEFAULT_SECTIONS = {
    "Income": SectionAlias("income"),
    "COGS": SectionAlias("cogs"),
    "Expenses": SectionAlias("opex"),
    "OtherIncome": SectionAlias("other_income"),     # UNVERIFIED: not in the captured file
    "OtherExpenses": SectionAlias("other_expense"),
}
# top-level groups that carry only a Summary; value = control name (None = the report's own label)
DEFAULT_DERIVED = {"GrossProfit": None, "NetOperatingIncome": None, "NetOtherIncome": None, "NetIncome": None}


def _label(cd, ref):
    if not isinstance(cd, list) or not cd or not isinstance(cd[0], dict) or not isinstance(cd[0].get("value"), str):
        raise AdapterError("UNCLASSIFIED_ROW", "row has no label in ColData[0]", ref)
    s = cd[0]["value"].strip()
    if not s:
        raise AdapterError("UNCLASSIFIED_ROW", "row has an empty label", ref)
    return s


def _raw_amount(cd):
    """-> the text of ColData[1].value, or None when the cell is missing."""
    if isinstance(cd, list) and len(cd) > 1 and isinstance(cd[1], dict):
        v = cd[1].get("value")
        return v if isinstance(v, str) else None
    return None


class QboReportAdapter:
    def __init__(self, path, company_id, sections=None, derived=None):
        self.path = Path(path)
        self.company_id = company_id
        self.sections = dict(DEFAULT_SECTIONS if sections is None else sections)
        self.derived = dict(DEFAULT_DERIVED if derived is None else derived)

    # ---- files ----
    def _files(self):
        p = self.path
        if p.is_file():
            files = [p]
        elif p.is_dir():
            files = sorted(x for x in p.iterdir() if x.is_file() and x.suffix.lower() == ".json")
        else:
            raise AdapterError("SOURCE_UNREADABLE", f"cannot open {p}: no such file or folder")
        if not files:
            raise AdapterError("SOURCE_UNREADABLE", f"no .json files in {p}")
        return files

    def _read(self, f):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise AdapterError("SOURCE_UNREADABLE", f"cannot parse {f.name}: {e}", f.name) from e
        prefix = ""
        if isinstance(doc, dict) and "Header" not in doc and isinstance(doc.get("data"), dict):
            doc, prefix = doc["data"], "data."
        if not isinstance(doc, dict) or not isinstance(doc.get("Header"), dict):
            raise AdapterError("SOURCE_UNREADABLE", "no Header: not a QuickBooks report", f.name)
        return doc, prefix

    def _period(self, f, header):
        """The calendar month the report covers, from its Header; must be one whole month. The file name is not used."""
        dates = []
        for key in ("StartPeriod", "EndPeriod"):
            m = _DATE.match(str(header.get(key)))
            if not m:
                raise AdapterError("SOURCE_UNREADABLE", f"Header.{key} is {header.get(key)!r}, not YYYY-MM-DD", f.name)
            dates.append(tuple(int(x) for x in m.groups()))
        (y1, m1, d1), (y2, m2, d2) = dates
        if (y1, m1) != (y2, m2) or d1 != 1 or d2 != monthrange(y2, m2)[1]:
            raise AdapterError("SOURCE_UNREADABLE", f"report covers {header['StartPeriod']}..{header['EndPeriod']}, "
                               "not one whole calendar month", f.name)
        return str(Period(y1, m1))

    @staticmethod
    def _check_columns(f, doc, prefix):
        cols = (doc.get("Columns") or {}).get("Column") if isinstance(doc.get("Columns"), dict) else None
        types = [c.get("ColType") for c in cols] if isinstance(cols, list) else None
        if types != ["Account", "Money"]:
            raise AdapterError("SOURCE_UNREADABLE", f"expected exactly an Account and a Money column "
                               f"(one total per report), got {types!r}", f"{f.name}#{prefix}Columns")

    # ---- amounts ----
    @staticmethod
    def _money(text, what, ref):
        if text is None or not text.strip():
            raise AdapterError("NON_NUMERIC", f"{what} has a blank amount", ref)
        t = text.strip()
        if not _AMOUNT.match(t):
            raise AdapterError("NON_NUMERIC", f"{what} has a non-numeric amount ({text!r})", ref)
        try:
            return money(Decimal(t))
        except InvalidOperation:      # unreachable after the regex; kept so nothing leaks as a bare exception
            raise AdapterError("NON_NUMERIC", f"{what} has a non-numeric amount ({text!r})", ref)

    # ---- main ----
    def load(self):
        files = self._files()
        records, controls, seen, basis = [], [], {}, None
        for f in files:
            doc, prefix = self._read(f)
            period = self._period(f, doc["Header"])
            if period in seen:
                raise AdapterError("DUPLICATE_PERIOD", f"period {period} appears in {seen[period]} and {f.name}", f.name)
            seen[period] = f.name
            b = doc["Header"].get("ReportBasis")
            if not isinstance(b, str) or not b.strip():
                raise AdapterError("SOURCE_UNREADABLE", "Header.ReportBasis is missing", f.name)
            if basis is None:
                basis = (b, f.name)
            elif b != basis[0]:
                raise AdapterError("SOURCE_UNREADABLE", f"ReportBasis is {b!r} here but {basis[0]!r} in {basis[1]}", f.name)
            self._check_columns(f, doc, prefix)
            self._read_report(f.name, prefix, doc, period, records, controls)
        order = sorted(range(len(records)), key=lambda i: (records[i].period, i))   # chronological, document order within a month
        corder = sorted(range(len(controls)), key=lambda i: (controls[i].period, i))
        return [records[i] for i in order], [controls[i] for i in corder]

    def _read_report(self, fname, prefix, doc, period, records, controls):
        base = f"{fname}#{prefix}Rows.Row"
        rows = (doc.get("Rows") or {}).get("Row") if isinstance(doc.get("Rows"), dict) else None
        if not isinstance(rows, list):
            raise AdapterError("SOURCE_UNREADABLE", "no Rows.Row list", f"{fname}#{prefix}Rows")
        found = []     # (label, path, amount, ref) for every Summary, to detect repeated labels

        def summary(row, ref, path):
            cd = (row.get("Summary") or {}).get("ColData") if isinstance(row.get("Summary"), dict) else None
            if cd is None:
                return None
            sref = f"{ref}.Summary.ColData[1]"
            label = _label(cd, f"{ref}.Summary.ColData[0]")
            return label, self._money(_raw_amount(cd), f"summary {label!r}", sref), sref

        def walk(rs, rref, alias, path):
            if not isinstance(rs, list):
                raise AdapterError("UNCLASSIFIED_ROW", "Rows.Row is not a list", rref)
            for i, row in enumerate(rs):
                ref = f"{rref}[{i}]"
                if not isinstance(row, dict):
                    raise AdapterError("UNCLASSIFIED_ROW", "row is not an object", ref)
                typ = row.get("type")
                if typ == "Data":
                    cd = row.get("ColData")
                    label = _label(cd, f"{ref}.ColData[0]")
                    if _TOTAL.match(label):
                        raise AdapterError("UNDECLARED_TOTAL", f"{label!r} looks like a total but is a Data row", ref)
                    amt = self._money(_raw_amount(cd), f"{label!r}", f"{ref}.ColData[1]")
                    records.append(Record(self.company_id, "qbo", period, alias.kind, [*path, label], label, amt,
                                          f"{ref}.ColData[1]"))
                elif typ == "Section":
                    hcd = (row.get("Header") or {}).get("ColData") if isinstance(row.get("Header"), dict) else None
                    label = _label(hcd, f"{ref}.Header.ColData[0]")
                    own = _raw_amount(hcd)
                    here = [*path, label]
                    if own is not None and own.strip():
                        records.append(Record(self.company_id, "qbo", period, alias.kind, here, label,
                                              self._money(own, f"parent {label!r} own balance", f"{ref}.Header.ColData[1]"),
                                              f"{ref}.Header.ColData[1]"))
                    sub = row.get("Rows")
                    if isinstance(sub, dict) and sub.get("Row"):
                        walk(sub["Row"], f"{ref}.Rows.Row", alias, here)
                    s = summary(row, ref, here)
                    if s is None:
                        raise AdapterError("UNCLASSIFIED_ROW", f"section {label!r} has no Summary", ref)
                    found.append((s[0], here, s[1], s[2]))
                else:
                    raise AdapterError("UNCLASSIFIED_ROW", f"row type {typ!r} is neither Data nor Section", ref)

        for i, row in enumerate(rows):
            ref = f"{base}[{i}]"
            if not isinstance(row, dict) or row.get("type") != "Section":
                raise AdapterError("UNCLASSIFIED_ROW", "a top-level row outside any section", ref)
            group = row.get("group")
            if group in self.sections:
                alias = self.sections[group]
                hcd = (row.get("Header") or {}).get("ColData") if isinstance(row.get("Header"), dict) else None
                label = _label(hcd, f"{ref}.Header.ColData[0]")
                own = _raw_amount(hcd)
                if own is not None and own.strip():
                    raise AdapterError("UNCLASSIFIED_ROW", f"top-level section {label!r} has an amount in its header",
                                       f"{ref}.Header.ColData[1]")
                top = alias.header or label
                walk((row.get("Rows") or {}).get("Row", []) if isinstance(row.get("Rows"), dict) else [],
                     f"{ref}.Rows.Row", alias, [top])
                s = summary(row, ref, [top])
                if s is None:
                    raise AdapterError("UNCLASSIFIED_ROW", f"section {label!r} has no Summary", ref)
                found.append((alias.total or s[0], None, s[1], s[2]))
            elif group in self.derived:
                if isinstance(row.get("Rows"), dict) and row["Rows"].get("Row"):
                    raise AdapterError("UNCLASSIFIED_ROW", f"derived row {group!r} unexpectedly has detail rows", ref)
                s = summary(row, ref, None)
                if s is None:
                    raise AdapterError("UNCLASSIFIED_ROW", f"derived row {group!r} has no Summary", ref)
                found.append((self.derived[group] or s[0], None, s[1], s[2]))
            else:
                raise AdapterError("UNCLASSIFIED_ROW", f"top-level group {group!r} is not in the alias table", ref)

        dup = {n for n, c in Counter(x[0] for x in found).items() if c > 1}
        for label, path, amt, sref in found:
            name = " > ".join([*path[:-1], label]) if (label in dup and path) else label
            if label in dup and path is None:
                raise AdapterError("UNCLASSIFIED_ROW", f"top-level summary {label!r} appears more than once", sref)
            controls.append(ControlTotal(period, name, amt, sref))
