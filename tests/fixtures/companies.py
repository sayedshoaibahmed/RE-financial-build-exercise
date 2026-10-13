"""Code-built company folders (company.yaml + mapping.yaml) for mapping tests."""
import yaml

from tests.fixtures.builders import default_rows, source, write

APPROVED = {"approved_by": "Tester", "approved_on": "2026-10-13"}

TINY_MAP = [  # account, section, function, nature
    ("Sales", "income", None, None),
    ("Fees/Misc: Setup", "income", None, None),
    ("Hosting", "cogs", None, None),
    ("Rent", "opex", "g_and_a", "facilities_office"),
    ("Salaries", "opex", "r_and_d", "payroll_benefits"),
    ("Payroll Taxes", "opex", "r_and_d", "payroll_benefits"),
    ("Marketing", "opex", "sales_marketing", "marketing_events"),
]


def entries(**approval):
    out = []
    for account, section, fn, nat in TINY_MAP:
        e = {"account": account, "section": section}
        if fn:
            e.update(function=fn, nature=nat)
        e.update(approval or APPROVED)
        out.append(e)
    return out


def write_company(root, rows=None, mapping=entries, company_yaml_extra=None, name="tiny"):
    """-> company_dir. Writes workbook, company.yaml and (if mapping is not None) mapping.yaml."""
    root.mkdir(parents=True, exist_ok=True)
    cdir = root / "companies" / name
    cdir.mkdir(parents=True)
    xlsx = root / "t.xlsx"
    write(xlsx, rows if rows is not None else default_rows())
    doc = {"source": source(path=str(xlsx))}
    doc.update(company_yaml_extra or {})
    (cdir / "company.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    if mapping is not None:
        m = mapping() if callable(mapping) else mapping
        (cdir / "mapping.yaml").write_text(yaml.safe_dump(m, sort_keys=False), encoding="utf-8")
    return cdir
