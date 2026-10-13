"""The shared category vocabulary: reference/categories.yaml plus explicit company extras."""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CATEGORIES_FILE = Path(__file__).resolve().parents[2] / "reference" / "categories.yaml"
_SLUG = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class Vocabulary:
    functions: dict   # id -> label
    natures: dict


def check_slug(value, what):
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ValueError(f"{what}: {value!r} is not a valid id (lowercase letters, digits, underscore; "
                         "must start with a letter)")


def load_vocabulary(extra_functions=(), extra_natures=()):
    raw = yaml.safe_load(CATEGORIES_FILE.read_text(encoding="utf-8"))
    out = {}
    for kind, extra in (("functions", extra_functions), ("natures", extra_natures)):
        cats = {e["id"]: e["label"] for e in raw[kind]}
        for x in extra:
            check_slug(x, f"categories_extra.{kind}")
            if x in cats:
                raise ValueError(f"categories_extra.{kind}: {x!r} already exists in the base vocabulary")
            cats[x] = x
        out[kind] = cats
    return Vocabulary(out["functions"], out["natures"])
