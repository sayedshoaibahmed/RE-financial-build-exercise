from pathlib import Path

import pytest
import yaml

from tests.fixtures import companies as co
from trend.categories import Vocabulary, load_vocabulary
from trend.config import ConfigError, load_company

SKILL = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "monthly-trend-analysis"
REF = SKILL / "reference"


def test_base_vocabulary_matches_plan_4_4_sizes():
    v = load_vocabulary()
    assert isinstance(v, Vocabulary)
    assert len(v.functions) == 8 and len(v.natures) == 11  # PLAN 4.4 / A19


def test_base_vocabulary_ids_and_examples_from_plan():
    v = load_vocabulary()
    assert "sales_marketing" in v.functions and "payroll_benefits" in v.natures
    assert "marketing_events" in v.natures  # PLAN 3.6 example
    assert all(k == k.lower() and " " not in k for k in list(v.functions) + list(v.natures))


def test_every_entry_has_label_and_definition():
    raw = yaml.safe_load((REF / "categories.yaml").read_text(encoding="utf-8"))
    assert set(raw) == {"functions", "natures"}
    for kind in raw.values():
        ids = [e["id"] for e in kind]
        assert len(ids) == len(set(ids))
        for e in kind:
            assert set(e) == {"id", "label", "definition"}
            assert e["label"].strip() and len(e["definition"].strip()) > 10


def test_human_doc_documents_every_machine_id():
    md = (REF / "categories.md").read_text(encoding="utf-8")
    v = load_vocabulary()
    for cid in list(v.functions) + list(v.natures):
        assert f"`{cid}`" in md, cid


def test_extras_extend_the_vocabulary_explicitly():
    v = load_vocabulary(extra_functions=("legal_ops",), extra_natures=("royalties",))
    assert "legal_ops" in v.functions and "royalties" in v.natures
    assert len(v.functions) == 9 and len(v.natures) == 12


def test_extras_cannot_redefine_existing_ids():
    with pytest.raises(ValueError):
        load_vocabulary(extra_functions=("g_and_a",))


@pytest.mark.parametrize("bad", ["Has Space", "UPPER", "", "9lead"])
def test_extras_must_be_slugs(bad):
    with pytest.raises(ValueError):
        load_vocabulary(extra_natures=(bad,))


def test_company_yaml_categories_extra_is_parsed(tmp_path):
    cdir = co.write_company(tmp_path, mapping=None,
                            company_yaml_extra={"categories_extra": {"functions": ["legal_ops"],
                                                                     "natures": ["royalties"]}})
    c = load_company(cdir)
    assert c.categories_extra.functions == ("legal_ops",) and c.categories_extra.natures == ("royalties",)


def test_categories_extra_is_optional_and_defaults_empty(tmp_path):
    c = load_company(co.write_company(tmp_path, mapping=None))
    assert c.categories_extra.functions == () and c.categories_extra.natures == ()


@pytest.mark.parametrize("extra", [
    {"functions": "legal_ops"},          # not a list
    {"colours": []},                     # unknown key
    {"functions": ["Bad Slug"]},
    {"functions": ["g_and_a"]},          # collides with base vocabulary
    {"functions": ["a_x", "a_x"]},       # duplicate
])
def test_categories_extra_validation(tmp_path, extra):
    cdir = co.write_company(tmp_path, mapping=None, company_yaml_extra={"categories_extra": extra})
    with pytest.raises(ConfigError):
        load_company(cdir)
