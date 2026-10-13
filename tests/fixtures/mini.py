"""A tiny parametrised company for metric edge cases: any list of periods (gaps allowed).

Accounts: Sales (income), Hosting (cogs, optional), Rent and Marketing (opex).
Control rows are written as literal numbers computed here, so reconciliation passes
unless `sales_leaf` deliberately differs from `sales`.
"""
from types import SimpleNamespace

import yaml

from tests.fixtures import builders as b
from trend.adapters.excel_pl import ExcelPLAdapter
from trend.categories import load_vocabulary
from trend.config import load_company
from trend.mapping import load_mapping, map_records
from trend.reconcile import reconcile

OK = {"approved_by": "Tester", "approved_on": "2026-10-13"}


def rows_for(sales, cogs, rent, mkt, with_cogs=True, sales_leaf=None):
    n = len(sales)
    z = [0] * n
    cogs = cogs if with_cogs else z
    t_inc = list(sales)
    gp = [a - c for a, c in zip(t_inc, cogs)]
    t_opex = [r + m for r, m in zip(rent, mkt)]
    opinc = [g - o for g, o in zip(gp, t_opex)]
    rows = [["Income", None, None], [None, "Sales", sales_leaf or sales], ["Total Income", None, t_inc]]
    if with_cogs:
        rows += [["Cost of Goods Sold", None, None], [None, "Hosting", cogs],
                 ["Total Cost of Goods Sold", None, cogs], [None, "Gross Profit", gp]]
    rows += [["Operating Expenses", None, None], [None, "Rent", rent], [None, "Marketing", mkt],
             ["Total Operating Expenses", None, t_opex], [None, "Operating Income", opinc],
             [None, "Net Income", opinc]]
    return rows


def build(tmp_path, periods, sales, cogs, rent, mkt, adjustments=None, windows=None, with_cogs=True,
          sales_leaf=None, with_mapping_in_recon=True):
    cdir = tmp_path / "companies" / "mini"
    cdir.mkdir(parents=True)
    xlsx = tmp_path / "mini.xlsx"
    b.write(xlsx, rows_for(sales, cogs, rent, mkt, with_cogs, sales_leaf), periods=periods)
    src = b.source(path=str(xlsx), groups=[])
    if with_cogs:
        src["derived_rows"] = dict(b.SOURCE["derived_rows"])
    else:
        src["sections"] = [s for s in src["sections"] if s["kind"] != "cogs"]
        src["derived_rows"] = {"operating_income": "Operating Income", "net_income": "Net Income"}
    doc = {"source": src}
    if adjustments is not None:
        doc["adjustments"] = adjustments
    if windows is not None:
        doc["windows"] = windows
    (cdir / "company.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    ents = [{"account": "Sales", "section": "income", **OK}]
    if with_cogs:
        ents.append({"account": "Hosting", "section": "cogs", **OK})
    ents += [{"account": "Rent", "section": "opex", "function": "g_and_a", "nature": "facilities_office", **OK},
             {"account": "Marketing", "section": "opex", "function": "sales_marketing",
              "nature": "marketing_events", **OK}]
    (cdir / "mapping.yaml").write_text(yaml.safe_dump(ents, sort_keys=False), encoding="utf-8")
    company = load_company(cdir)
    records, controls = ExcelPLAdapter(company.source, company.company_id).load()
    mapped = map_records(records, load_mapping(cdir, load_vocabulary()), company, cdir)
    recon = reconcile(records, controls, company.source, mapped=mapped if with_mapping_in_recon else None)
    return SimpleNamespace(company=company, records=records, controls=controls, mapped=mapped, recon=recon)


P6 = ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"]
