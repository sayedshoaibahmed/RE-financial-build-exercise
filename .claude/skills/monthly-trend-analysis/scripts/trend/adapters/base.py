"""Adapter protocol: every source yields the same flat table."""
from typing import Protocol, runtime_checkable


class AdapterError(Exception):
    """Hard failure while reading a source. `code` is one of the BLOCKED reasons in PLAN 5
    (UNDECLARED_TOTAL, UNCLASSIFIED_ROW, NON_NUMERIC, NO_CACHED_VALUES, DUPLICATE_PERIOD)
    or SOURCE_UNREADABLE."""

    def __init__(self, code, message, source_ref=None):
        self.code = code
        self.source_ref = source_ref
        where = f" [{source_ref}]" if source_ref else ""
        super().__init__(f"{code}{where}: {message}")


@runtime_checkable
class Adapter(Protocol):
    def load(self):
        """Return (records: list[Record], controls: list[ControlTotal])."""
        ...
