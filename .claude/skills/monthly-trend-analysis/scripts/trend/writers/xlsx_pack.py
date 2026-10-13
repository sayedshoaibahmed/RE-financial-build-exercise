"""trend_pack.xlsx (PLAN 6.1). Values only: no formulas, no macros, no charts (charts are on the cut list).

Sheets follow PLAN, plus `Data_accounts` (PLAN 4.4's account-level pivot table). `Data` is one row per fact,
so a reviewer can pivot any metric, basis, view or period. Months run oldest -> newest. Written in
openpyxl's write-only mode (134k fact rows), with fixed document properties so two runs give identical cells.
"""
import re
from datetime import datetime

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from .. import __version__
from ..basis import apply_adjustments
from ..categories import load_vocabulary
from ..digest import ADJ_MONTH_ROWS, HEADLINE
from ..facts import BASIS_NAME, month_name, observed_text, parse_fact_id, to_cell, unit_of
from ..metrics import NA, shift, slug

SHEETS = ["Cover", "Summary", "Revenue", "Margins", "Opex_by_function", "Opex_by_nature", "Opex_detail", "Windows",
          "Adjustments", "Flags", "Reconciliation", "Data", "Data_accounts", "Commentary", "Assumptions"]
FMT = {"usd": "#,##0.00", "pct": '0.00"%"', "pp": '0.00" pp"'}
BOLD = Font(bold=True)
BANNER = Font(bold=True, color="C00000")
FIXED_TIME = datetime(2000, 1, 1)
DRAFT = "DRAFT: computed by scripts from the source workbook. Not reviewed. For reviewer completion."


def _c(ws, value, fmt=None, font=None):
    cell = WriteOnlyCell(ws, value=value)
    if fmt:
        cell.number_format = fmt
    if font:
        cell.font = font
    return cell


def _h(ws, *values):
    return [_c(ws, v, font=BOLD) for v in values]


def _fid(basis, key, view, period):
    return ".".join([basis, key] + ([view] if view else []) + [period])


def _unit(key, view, basis="rep", period="2000-01"):
    return unit_of(parse_fact_id(_fid(basis, key, view, period)))


def _val(ws, facts, basis, key, view, period):
    """A cell for a fact: number (formatted), n/a text, or blank when the fact does not exist."""
    v = facts.get(_fid(basis, key, view, period))
    if v is None:
        return None
    u = _unit(key, view, basis, period)
    return _c(ws, to_cell(v, u), FMT[u] if not isinstance(v, NA) else None)


def _sheet(wb, name, widths=None, freeze=None):
    ws = wb.create_sheet(name)
    for col, w in (widths or {}).items():
        ws.column_dimensions[col].width = w
    if freeze:
        ws.freeze_panes = freeze
    return ws


def _matrix(wb, name, title, note, periods, facts, specs, bases=("rep", "adj")):
    """specs: (label, key, view_label, fact_view). One row per spec and basis, one column per month."""
    ws = _sheet(wb, name, {"A": 44, "B": 11, "C": 16, "D": 34}, "E5")
    ws.append([_c(ws, title, font=BOLD)])
    ws.append([note])
    ws.append([])
    ws.append(_h(ws, "Metric", "Basis", "View", "Fact id (without period)", *periods))
    for label, key, view_label, fview in specs:
        for b in bases:
            prefix = ".".join([b, key] + ([fview] if fview else []))
            ws.append([label, BASIS_NAME[b].capitalize(), view_label, prefix] + [_val(ws, facts, b, key, fview, p) for p in periods])
    return ws


# ---------------------------------------------------------------- the sheets

def _cover(wb, company, metrics, flagset, meta):
    ws = _sheet(wb, "Cover", {"A": 30, "B": 70})
    ws.append([_c(ws, f"Monthly trend pack: {company.company_id}", font=BOLD)])
    ws.append([_c(ws, DRAFT, font=BANNER)])
    ws.append([])
    rows = [("Company", company.company_id), ("Status", "OK"), ("As-of month", metrics.periods[-1]),
            ("First month", metrics.periods[0]), ("Months of data", str(len(metrics.periods))),
            ("Window lengths (months)", ", ".join(str(n) for n in metrics.window_lengths)),
            ("Window end", metrics.window_end), ("Adjustments configured", str(len(metrics.adjustments))),
            ("Flags", str(len(flagset.flags))), ("Input workbook SHA-256", meta["input_sha256"]),
            ("Config SHA-256", meta["config_sha256"]), ("Git SHA", meta.get("git_sha", "unavailable")),
            ("Tool version", __version__)]
    for k, v in rows:
        ws.append([_c(ws, k, font=BOLD), v])


def _summary(wb, metrics, adj_flag_ids):
    facts = metrics.facts
    ws = _sheet(wb, "Summary", {"A": 52, "B": 8, "C": 18, "D": 18, "E": 18, "F": 20, "G": 20, "H": 18, "I": 14, "J": 18, "K": 14})
    latest = metrics.periods[-1]
    prior = shift(latest, -1)
    ws.append([_c(ws, "Summary: reported and adjusted side by side", font=BOLD)])
    ws.append([DRAFT])
    ws.append([])

    def triple(kind, key, win, p):
        spec = {"amount": (key, win), "ratio": (key, win),
                "pctrev": (key, f"{win}_pct_rev" if win else "pct_rev")}[kind]
        r, a, d = (_val(ws, facts, b, spec[0], spec[1], p) for b in ("rep", "adj", "delta"))
        return r, a, d

    ws.append([_c(ws, f"Latest month: {month_name(latest)}", font=BOLD)])
    ws.append(_h(ws, "Metric", "Unit", "Reported", "Adjusted", "Delta (adj - rep)", "Reported, prior month",
                 "Adjusted, prior month", "MoM change, reported", "MoM %, reported", "MoM change, adjusted", "MoM %, adjusted"))
    for label, key, kind in HEADLINE:
        r, a, d = triple(kind, key, None, latest)
        pr, pa, _ = triple(kind, key, None, prior) if prior in metrics.periods else (None, None, None)
        view = {"amount": "mom_abs", "ratio": "mom_pp", "pctrev": "pct_rev_mom_pp"}[kind]
        k2 = key
        mom = [_val(ws, facts, b, k2, view, latest) for b in ("rep", "adj")]
        pct = [_val(ws, facts, b, k2, "mom_pct", latest) if kind == "amount" else None for b in ("rep", "adj")]
        ws.append([label, "usd" if kind == "amount" else "pct", r, a, d, pr, pa, mom[0], pct[0], mom[1], pct[1]])
    ws.append([])
    for n in metrics.window_lengths:
        win = f"tw{n}"
        ws.append([_c(ws, f"{n}-month window ending {metrics.window_end}", font=BOLD)])
        ws.append(_h(ws, "Metric", "Unit", "Reported", "Adjusted", "Delta (adj - rep)"))
        for label, key, kind in HEADLINE:
            ws.append([label, "usd" if kind == "amount" else "pct", *triple(kind, key, win, metrics.window_end)])
        ws.append([])
    n0 = metrics.window_lengths[0]
    for p in sorted({a.period for a in metrics.adjustments if a.present and a.reported_amount != 0}):
        ws.append([_c(ws, f"Adjusted period {p} shown both ways", font=BOLD)])
        ws.append(_h(ws, "Metric", "Unit", "Reported", "Adjusted", "Delta (adj - rep)"))
        for label, key, kind in ADJ_MONTH_ROWS:
            ws.append([f"{label} (month)", "usd" if kind == "amount" else "pct", *triple(kind, key, None, p)])
        if _fid("rep", "opinc", f"tw{n0}", p) in facts:
            for label, key, kind in ADJ_MONTH_ROWS:
                ws.append([f"{label} ({n0}-month window ending {p})", "usd" if kind == "amount" else "pct",
                           *triple(kind, key, f"tw{n0}", p)])
        for a in metrics.adjustments:
            if a.period == p and a.present and a.reported_amount != 0:
                ws.append([f"Excluded: {a.account}", "usd", _c(ws, a.reported_amount, FMT["usd"]),
                           _c(ws, a.adjusted_amount, FMT["usd"]), _c(ws, a.delta, FMT["usd"])])
        ws.append([])


def _flags(wb, flagset):
    ws = _sheet(wb, "Flags", {"A": 22, "B": 16, "C": 10, "D": 10, "E": 9, "F": 40, "G": 60, "H": 50, "I": 90, "J": 50, "K": 24})
    ws.append([_c(ws, "Flags", font=BOLD)])
    ws.append(["Counts by type: " + ", ".join(f"{t}: {n}" for t, n in flagset.counts.items())])
    ws.append(["Counts by severity: " + ", ".join(f"{s}: {n}" for s, n in flagset.by_severity.items())])
    ws.append([])
    ws.append(_h(ws, "Flag id", "Type", "Severity", "Basis", "Period", "Subject", "Observed", "Threshold", "Reason",
                 "Fact ids", "Source cells"))
    for f in flagset.flags:
        ws.append([f.id, f.type, f.severity, f.basis, f.period, f.subject, observed_text(f.observed),
                   observed_text(f.threshold), f.reason, ", ".join(f.fact_ids), ", ".join(f.source_refs)])
    ws.append([])
    ws.append([_c(ws, "Suppressed negatives: below negative_min_abs_amount, so not flagged; they stay in the data and every total", font=BOLD)])
    ws.append(_h(ws, "Account", "Period", "Amount"))
    for s in flagset.suppressed_negatives:
        ws.append([s["account"], s["period"], _c(ws, s["amount"], FMT["usd"])])


def _reconciliation(wb, recon):
    ws = _sheet(wb, "Reconciliation", {"A": 9, "B": 34, "C": 9, "D": 18, "E": 18, "F": 14, "G": 8, "H": 28, "I": 26})
    ws.append([_c(ws, f"Reconciliation: every control recomputed from the records and compared with the workbook. Status {recon.status}", font=BOLD)])
    ws.append([])
    ws.append(_h(ws, "Control", "Name", "Period", "Expected (workbook)", "Actual (recomputed)", "Difference", "Status",
                 "Source cell", "Note"))
    for r in sorted(recon.results, key=lambda r: (r.period, r.control_id)):
        ws.append([r.control_id, r.control_name, r.period, *(_c(ws, None if x is None else to_cell(x, "usd"), FMT["usd"])
                                                            for x in (r.expected, r.actual, r.difference)),
                   "OK" if r.ok else "FAIL", r.source_ref, r.reason])
    ws.append([])
    ws.append([_c(ws, "Controls not applicable to this company", font=BOLD)])
    ws.append(_h(ws, "Control", "Reason"))
    for x in recon.not_applicable:
        ws.append([x.control_id, x.reason])


def _adjustments(wb, metrics):
    ws = _sheet(wb, "Adjustments", {"A": 36, "B": 9, "C": 18, "D": 18, "E": 18, "F": 13, "G": 60})
    ws.append([_c(ws, "Adjustments: the adjusted basis sets these (account, month) amounts to zero", font=BOLD)])
    ws.append([])
    ws.append(_h(ws, "Account", "Period", "Reported amount", "Adjusted amount", "Delta", "Data present", "Reason"))
    for a in metrics.adjustments:
        ws.append([a.account, a.period, _c(ws, a.reported_amount, FMT["usd"]), _c(ws, a.adjusted_amount, FMT["usd"]),
                   _c(ws, a.delta, FMT["usd"]), "yes" if a.present else "no", a.reason])


def _data(wb, metrics):
    ws = _sheet(wb, "Data", {"A": 62, "B": 10, "C": 16, "D": 36, "E": 18, "F": 9, "G": 20, "H": 6, "I": 40}, "A2")
    ws.append(_h(ws, "Fact id", "Basis", "Metric", "Dimension", "View", "Period", "Value", "Unit", "Note"))
    for fid, v in metrics.facts.items():
        p = parse_fact_id(fid)
        u = unit_of(p)
        na = isinstance(v, NA)
        note = (v.reason + (": missing " + ", ".join(v.missing) if v.missing else "")) if na else None
        ws.append([fid, BASIS_NAME[p["basis"]], p["metric"], p["dimension"], p["view"] or None, p["period"],
                   None if na else to_cell(v, u), u, note])


def _data_accounts(wb, company, metrics, mapped):
    ws = _sheet(wb, "Data_accounts", {"A": 10, "B": 9, "C": 8, "D": 44, "E": 60, "F": 20, "G": 28, "H": 16, "I": 16, "J": 30}, "A2")
    ws.append(_h(ws, "Basis", "Period", "Section", "Account", "Path", "Function", "Nature", "Amount", "Revenue", "Group"))
    keep = set(metrics.periods)
    rep = [m for m in mapped if m.record.period in keep]
    adj, _ = apply_adjustments(rep, company.adjustments)
    for basis, tok, recs in (("reported", "rep", rep), ("adjusted", "adj", adj)):
        for m in sorted(recs, key=lambda m: m.record.period):         # stable: sheet order within a month
            r = m.record
            ws.append([basis, r.period, r.section, r.account, " > ".join(r.account_path), m.function, m.nature,
                       to_cell(r.amount, "usd"), to_cell(metrics.facts[_fid(tok, "rev.total", None, r.period)], "usd"),
                       m.group])


def _commentary(wb):
    ws = _sheet(wb, "Commentary", {"A": 120})
    ws.append([_c(ws, "Commentary", font=BOLD)])
    ws.append(["DRAFT commentary has not been generated yet. It is written by the skill from digest.json and "
               "rendered into this sheet by a later step."])


def _assumptions(wb, flagset):
    ws = _sheet(wb, "Assumptions", {"A": 130})
    ws.append([_c(ws, "Assumptions and conventions", font=BOLD)])
    lines = [
        "Amounts keep the sign the source presents. Contra lines such as Interest Income are marked expected_negative in mapping.yaml.",
        "Reported basis = the source figures. Adjusted basis = the same records with each configured (account, month) amount set to zero; every metric is recomputed on those records (sheet Adjustments).",
        "delta = adjusted minus reported.",
        "Percentages are in percent units; pp is a difference of two percentages (percentage points). Amounts show 2 decimals, ratios 4.",
        "A trailing window exists only when every month in it has data; otherwise the cell reads 'insufficient data' and names the missing months. Window ratios are ratios of sums.",
        "Month-on-month change is against the calendar month before; it is n/a when that month is missing or the base is zero.",
        "Operating income is as the source workbook defines it: it includes the interest and tax lines it presents inside operating expenses.",
        "COGS is as booked in the source. Function and nature groupings cover operating expenses only, and each adds up to total operating expenses (controls C9a, C9b).",
        "Currency is not stated in the source workbook. Fiscal year is taken as the calendar year; no fiscal-year view is built.",
        "Every control is recomputed with a tolerance of 0.01; the run stops (BLOCKED) if any control fails.",
        "This workbook holds values only: no formulas, macros or charts.",
    ] + [f"Threshold {k} = {v}" for k, v in flagset.thresholds.items()]
    for ln in lines:
        ws.append([ln])


# ---------------------------------------------------------------- entry point

def _dimensions(metrics):
    first = metrics.periods[0]
    dims = {"rev.stream": [], "rev.mix": [], "opex.fn": [], "opex.nat": [], "opex.line": []}
    for fid in metrics.facts:
        if fid.startswith("rep.") and fid.endswith("." + first):
            p = parse_fact_id(fid)
            if p["metric"] in dims and not p["view"]:
                dims[p["metric"]].append(p["dimension"])
    return dims


def write_workbook(path, company, metrics, flagset, recon, mapped, meta):
    facts, periods = metrics.facts, list(metrics.periods)
    n0 = metrics.window_lengths[0]
    dims = _dimensions(metrics)
    vocab = load_vocabulary(company.categories_extra.functions, company.categories_extra.natures)
    stream_names = {slug(m.group): m.group for m in mapped if m.record.section == "income"}
    line_names = {slug(m.record.account): m.record.account for m in mapped if m.record.section == "opex"}
    name = lambda d, k: d.get(k, k)

    wb = Workbook(write_only=True)
    wb.properties.creator = "trend"
    wb.properties.created = FIXED_TIME
    wb.properties.modified = FIXED_TIME

    _cover(wb, company, metrics, flagset, meta)
    _summary(wb, metrics, None)

    rev = [("Revenue", "rev.total", "value", None), ("Revenue", "rev.total", "mom_abs", "mom_abs"),
           ("Revenue", "rev.total", "mom_pct", "mom_pct")]
    for s in dims["rev.stream"]:
        lab = name(stream_names, s)
        rev += [(lab, f"rev.stream.{s}", "value", None), (lab, f"rev.stream.{s}", "mom_pct", "mom_pct")]
    rev += [(f"Mix: {name(stream_names, s)}", f"rev.mix.{s}", "value", None) for s in dims["rev.mix"]]
    _matrix(wb, "Revenue", "Revenue in total and by stream", "Months oldest to newest. Mix is each stream as a % of total revenue.",
            periods, facts, rev)

    mg = []
    for lab, key, kind in (("Gross profit", "gp", "a"), ("Gross margin", "gm_pct", "r"), ("Operating income", "opinc", "a"),
                           ("Operating margin", "opinc_margin_pct", "r"), ("Net income", "net_income", "a")):
        mg.append((lab, key, "value", None))
        mg += [(lab, key, "mom_abs", "mom_abs"), (lab, key, "mom_pct", "mom_pct")] if kind == "a" else [(lab, key, "mom_pp", "mom_pp")]
    _matrix(wb, "Margins", "Gross and operating margin, operating income", "Ratios are percent; MoM of a ratio is in percentage points.",
            periods, facts, mg)

    def opex_specs(kind, labels):
        specs = []
        for key, lab in [("opex.total", "Total operating expenses")] + [(f"opex.{kind}.{d}", labels.get(d, d)) for d in dims[f"opex.{kind}"]]:
            specs += [(lab, key, "value", None), (lab, key, "pct_rev", "pct_rev"), (lab, key, "mom_abs", "mom_abs"),
                      (lab, key, "mom_pct", "mom_pct"), (lab, key, "pct_rev_mom_pp", "pct_rev_mom_pp"),
                      (lab, key, "window", f"tw{n0}"), (lab, key, "window_pct_rev", f"tw{n0}_pct_rev")]
        return specs

    note = f"Window rows are the {n0}-month trailing window ending each month. pct_rev = % of revenue."
    _matrix(wb, "Opex_by_function", "Operating expenses by function (who spends)", note, periods, facts, opex_specs("fn", vocab.functions))
    _matrix(wb, "Opex_by_nature", "Operating expenses by nature (what the money is spent on)", note, periods, facts, opex_specs("nat", vocab.natures))
    detail = []
    for d in dims["opex.line"]:
        lab = name(line_names, d)
        detail += [(lab, f"opex.line.{d}", "value", None), (lab, f"opex.line.{d}", "pct_rev", "pct_rev"),
                   (lab, f"opex.line.{d}", "mom_abs", "mom_abs"), (lab, f"opex.line.{d}", "mom_pct", "mom_pct")]
    _matrix(wb, "Opex_detail", "Operating expenses by account", "Every original account line, as in the source.", periods, facts, detail)

    # Windows: one block per configured length
    ws = _sheet(wb, "Windows", {"A": 44, "B": 11, "C": 16, "D": 34}, "E1")
    ws.append([_c(ws, f"Trailing windows. Headline window ends {metrics.window_end}.", font=BOLD)])
    for n in metrics.window_lengths:
        ws.append([])
        ws.append([_c(ws, f"{n}-month trailing window, ending each month (headline window ends {metrics.window_end})", font=BOLD)])
        ws.append(_h(ws, "Metric", "Basis", "View", "Fact id (without period)", *[p for p in periods if p <= metrics.window_end]))
        wp = [p for p in periods if p <= metrics.window_end]
        for lab, key, kind in HEADLINE:
            view = f"tw{n}_pct_rev" if kind == "pctrev" else f"tw{n}"
            for b in ("rep", "adj"):
                ws.append([lab, BASIS_NAME[b].capitalize(), "value", ".".join([b, key, view])] + [_val(ws, facts, b, key, view, p) for p in wp])

    _adjustments(wb, metrics)
    _flags(wb, flagset)
    _reconciliation(wb, recon)
    _data(wb, metrics)
    _data_accounts(wb, company, metrics, mapped)
    _commentary(wb)
    _assumptions(wb, flagset)
    assert wb.sheetnames == SHEETS, wb.sheetnames
    wb.save(path)
