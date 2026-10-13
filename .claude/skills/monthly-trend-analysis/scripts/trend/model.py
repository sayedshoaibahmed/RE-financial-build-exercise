"""Core data types (PLAN 3.1)."""
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import total_ordering

_CENT = Decimal("0.01")


def money(x):
    """float/int/Decimal -> Decimal rounded half-up to cents. repr() strips float noise."""
    if isinstance(x, bool) or not isinstance(x, (int, float, Decimal)):
        raise TypeError(f"money() needs a number, got {type(x).__name__}")
    d = x if isinstance(x, Decimal) else Decimal(repr(x))
    return d.quantize(_CENT, rounding=ROUND_HALF_UP)


@total_ordering
@dataclass(frozen=True)
class Period:
    year: int
    month: int

    @classmethod
    def parse(cls, text):
        m = re.fullmatch(r"(\d{4})-(\d{2})", text)
        if not m or not 1 <= int(m.group(2)) <= 12:
            raise ValueError(f"period must look like YYYY-MM, got {text!r}")
        return cls(int(m.group(1)), int(m.group(2)))

    def __str__(self):
        return f"{self.year:04d}-{self.month:02d}"

    def __lt__(self, other):
        return (self.year, self.month) < (other.year, other.month)


def _need_decimal(v, what):
    if not isinstance(v, Decimal):
        raise TypeError(f"{what} must be Decimal, got {type(v).__name__}")


@dataclass
class Record:
    company_id: str
    source: str
    period: str            # YYYY-MM
    section: str           # income | cogs | opex | other_income | other_expense
    account_path: list     # a list, never a "/"-joined string
    account: str           # leaf label, stripped
    amount: Decimal        # 2dp, sign as presented
    source_ref: str        # e.g. "Income Statement!C65"

    def __post_init__(self):
        _need_decimal(self.amount, "Record.amount")
        if not isinstance(self.account_path, list):
            raise TypeError("Record.account_path must be a list")


@dataclass
class ControlTotal:
    period: str
    control_name: str
    amount: Decimal
    source_ref: str

    def __post_init__(self):
        _need_decimal(self.amount, "ControlTotal.amount")


class MetricsError(Exception):
    """Metrics cannot be produced (bad arguments, unknown adjustment account, incomplete reconciliation)."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(f"{code}: {message}")
