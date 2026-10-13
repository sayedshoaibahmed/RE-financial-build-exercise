from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import oracle
from trend.adapters.excel_pl import ExcelPLAdapter
from trend.categories import load_vocabulary
from trend.config import load_company
from trend.mapping import load_mapping, map_records
from trend.reconcile import reconcile

ROOT = Path(__file__).resolve().parents[2]
ABC_DIR = ROOT / "companies" / "abc_inc"


@pytest.fixture(scope="session")
def abc():
    """ABC loaded, mapped and reconciled once per test session."""
    company = load_company(ABC_DIR)
    records, controls = ExcelPLAdapter(company.source, company.company_id).load()
    mapping = load_mapping(ABC_DIR, load_vocabulary())
    mapped = map_records(records, mapping, company, ABC_DIR)
    recon = reconcile(records, controls, company.source, mapped=mapped)
    return SimpleNamespace(company=company, records=records, controls=controls, mapping=mapping,
                           mapped=mapped, recon=recon, o=oracle.scan(), dir=ABC_DIR)


@pytest.fixture(scope="session")
def abc_metrics(abc):
    from trend.metrics import compute_metrics
    return compute_metrics(abc.mapped, abc.recon, abc.company)


@pytest.fixture(scope="session")
def abc_flagset(abc, abc_metrics):
    from trend.flags import compute_flags
    return compute_flags(abc.mapped, abc.recon, abc_metrics, abc.company)
