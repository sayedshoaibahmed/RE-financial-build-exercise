"""Excel P&L sheet -> flat table (PLAN 3.2). Row classification is strict: anything
that matches no rule is a hard error, never a guess."""
import re
from datetime import date, datetime

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter

from ..model import ControlTotal, Period, Record, money
from .base import AdapterError

_TOTAL = re.compile(r"^total\b", re.IGNORECASE)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


class ExcelPLAdapter:
    def __init__(self, source, company_id):
        self.cfg = source
        self.company_id = company_id

    # ---- helpers ----
    def _ref(self, col, row):
        return f"{self.cfg.sheet}!{get_column_letter(col)}{row}"

    def _open(self):
        cfg = self.cfg
        try:
            vals = openpyxl.load_workbook(cfg.path, data_only=True)
            forms = openpyxl.load_workbook(cfg.path, data_only=False)
        except Exception as e:  # missing file, not an xlsx, ...
            raise AdapterError("SOURCE_UNREADABLE", f"cannot open {cfg.path}: {e}") from e
        if cfg.sheet not in vals.sheetnames:
            raise AdapterError("SOURCE_UNREADABLE",
                               f"sheet {cfg.sheet!r} not found; sheets: {', '.join(vals.sheetnames)}")
        return vals[cfg.sheet], forms[cfg.sheet]

    def _periods(self, ws):
        """{column index: 'YYYY-MM'} from the header row only (never max_column)."""
        cfg = self.cfg
        first = max(column_index_from_string(cfg.section_label_col),
                    column_index_from_string(cfg.account_label_col)) + 1
        cols, seen = {}, {}
        for c in range(first, ws.max_column + 1):
            v = ws.cell(cfg.header_row, c).value
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            ref = self._ref(c, cfg.header_row)
            if isinstance(v, (datetime, date)):
                p = Period(v.year, v.month)
            elif isinstance(v, str):
                try:
                    d = datetime.strptime(v.strip(), cfg.period_header_format)
                except ValueError:
                    raise AdapterError("UNCLASSIFIED_ROW", f"header cell {v!r} is not a period "
                                       f"({cfg.period_header_format})", ref)
                p = Period(d.year, d.month)
            else:
                raise AdapterError("UNCLASSIFIED_ROW", f"header cell {v!r} is not a period", ref)
            key = str(p)
            if key in seen:
                raise AdapterError("DUPLICATE_PERIOD", f"period {key} appears in columns "
                                   f"{get_column_letter(seen[key])} and {get_column_letter(c)}", ref)
            seen[key] = c
            cols[c] = key
        if not cols:
            raise AdapterError("UNCLASSIFIED_ROW", "no period columns found in the header row",
                               f"{cfg.sheet}!row {cfg.header_row}")
        return cols

    # ---- main ----
    def load(self):
        cfg = self.cfg
        ws, wf = self._open()
        cols = self._periods(ws)
        order = sorted(cols, key=lambda c: cols[c])  # chronological, whatever the sheet order
        sec_c = column_index_from_string(cfg.section_label_col)
        acc_c = column_index_from_string(cfg.account_label_col)
        sections = {s.header: s for s in cfg.sections}
        sec_totals = {s.total: s for s in cfg.sections}
        groups = {g.header: g for g in cfg.groups}
        grp_totals = {g.total: g for g in cfg.groups}
        derived = set(cfg.derived_rows)

        records, controls = [], []
        cur_sec = cur_grp = None

        def cells(r):
            """-> list of (col, value) in chronological order; raises on missing caches."""
            out = []
            for c in order:
                v = ws.cell(r, c).value
                if v is None:
                    f = wf.cell(r, c).value
                    if isinstance(f, str) and f.startswith("="):
                        raise AdapterError("NO_CACHED_VALUES", "formula has no cached value; "
                                           "open and save in Excel, then retry", self._ref(c, r))
                out.append((c, v))
            return out

        def numeric_or_raise(r, label):
            cs = cells(r)
            for c, v in cs:
                if not _is_num(v):
                    raise AdapterError("NON_NUMERIC", f"{label!r} has a blank or non-numeric value "
                                       f"({v!r}) for {cols[c]}", self._ref(c, r))
            return cs

        def has_numbers(r):
            return any(_is_num(ws.cell(r, c).value) for c in order)

        def add_control(r, label):
            for c, v in numeric_or_raise(r, label):
                controls.append(ControlTotal(cols[c], label, money(v), self._ref(c, r)))

        def close_group_or_raise(r):
            if cur_grp is not None:
                raise AdapterError("UNCLASSIFIED_ROW", f"group {cur_grp!r} was not closed by "
                                   f"{groups[cur_grp].total!r}", self._ref(acc_c, r))

        for r in range(cfg.header_row + 1, ws.max_row + 1):
            a, b = _text(ws.cell(r, sec_c).value), _text(ws.cell(r, acc_c).value)
            if a is None and b is None:
                if has_numbers(r):
                    raise AdapterError("UNCLASSIFIED_ROW", "numbers on a row without a label",
                                       self._ref(acc_c, r))
                continue
            if a is not None and b is not None:
                raise AdapterError("UNCLASSIFIED_ROW", f"labels in both columns ({a!r}, {b!r})",
                                   self._ref(sec_c, r))

            # ---- section-level label (column A) ----
            if a is not None:
                ref = self._ref(sec_c, r)
                if a in sections:
                    if has_numbers(r):
                        raise AdapterError("UNCLASSIFIED_ROW", f"section header {a!r} has numbers", ref)
                    close_group_or_raise(r)
                    cur_sec, cur_grp = sections[a], None
                elif a in sec_totals:
                    if cur_sec is None or sec_totals[a] is not cur_sec:
                        raise AdapterError("UNCLASSIFIED_ROW", f"total {a!r} is not inside its section", ref)
                    close_group_or_raise(r)
                    add_control(r, a)
                    cur_sec = None
                elif _TOTAL.match(a):
                    raise AdapterError("UNDECLARED_TOTAL", f"{a!r} looks like a total but is not "
                                       "declared in config", ref)
                else:
                    raise AdapterError("UNCLASSIFIED_ROW", f"{a!r} matches no declared section", ref)
                continue

            # ---- account-level label (column B) ----
            ref = self._ref(acc_c, r)
            if b in groups:
                if has_numbers(r):
                    raise AdapterError("UNCLASSIFIED_ROW", f"group header {b!r} has numbers", ref)
                if cur_sec is None or cur_grp is not None:
                    raise AdapterError("UNCLASSIFIED_ROW", f"group header {b!r} is not directly "
                                       "inside a section", ref)
                cur_grp = b
            elif b in grp_totals:
                if cur_grp is None or groups[cur_grp] is not grp_totals[b]:
                    raise AdapterError("UNCLASSIFIED_ROW", f"group total {b!r} without its group", ref)
                add_control(r, b)
                cur_grp = None
            elif b in derived:
                add_control(r, b)
            elif _TOTAL.match(b):
                raise AdapterError("UNDECLARED_TOTAL", f"{b!r} looks like a total but is not "
                                   "declared in config", ref)
            elif cur_sec is None:
                raise AdapterError("UNCLASSIFIED_ROW", f"{b!r} is outside any section", ref)
            else:
                cs = cells(r)
                if all(v is None for _, v in cs):
                    raise AdapterError("UNCLASSIFIED_ROW", f"{b!r} has no numbers and is not a "
                                       "declared header", ref)
                cs = numeric_or_raise(r, b)
                path = [cur_sec.header] + ([cur_grp] if cur_grp else []) + [b]
                for c, v in cs:
                    records.append(Record(self.company_id, "excel", cols[c], cur_sec.kind,
                                          list(path), b, money(v), self._ref(c, r)))
        close_group_or_raise(ws.max_row)
        return records, controls
