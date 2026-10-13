"""The run / check flow behind trend_cli.py.

run:   load -> map -> reconcile -> metrics -> flags -> digest -> files in <out>/<company_id>/<run_id>/
check: load -> map -> reconcile only (writes nothing)

If reconciliation, the adapter or the mapping fails, the run is BLOCKED: only blocked_report.json (plus
unmapped_accounts.yaml for UNMAPPED_ACCOUNT) is written. No metrics, flags, digest or workbook.
Bad arguments / config raise UsageError, ConfigError or MetricsError (exit 2) before anything is written.
"""
import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .adapters.base import AdapterError
from .adapters.excel_pl import ExcelPLAdapter
from .categories import load_vocabulary
from .config import ConfigError, load_company
from .digest import build_digest
from .flags import blocked_flag, compute_flags
from .mapping import MappingError, load_mapping, map_records
from .metrics import _valid_period, compute_metrics, window_from_period_range
from .model import MetricsError
from .reconcile import BlockedError, reconcile
from .writers.json_out import write_blocked_report, write_digest, write_metrics_json
from .writers.xlsx_pack import write_workbook

EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NOT_IMPLEMENTED = 0, 2, 3, 70
SKILL_DIR = Path(__file__).resolve().parents[2]
SUCCESS_FILES = ("digest.json", "metrics.json", "trend_pack.xlsx")


class UsageError(Exception):
    """Bad command-line usage (exit 2)."""


@dataclass
class RunResult:
    status: str                 # OK | BLOCKED
    exit_code: int
    out_dir: Path
    files: list = field(default_factory=list)
    reason: str = None
    message: str = None


@dataclass
class CheckResult:
    status: str                 # OK | BLOCKED
    recon: object = None
    error: Exception = None


def default_out_base(company_dir):
    """PLAN: outputs/<company_id>/<run_id>/ next to the companies/ folder."""
    return Path(company_dir).resolve().parent.parent / "outputs"


def _run_id(as_of, lengths, end):
    return f"{as_of}_w{'+'.join(str(n) for n in lengths)}-{end}"


def _sha_file(path):
    """Plain SHA-256 of one file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha_config(*paths):
    """SHA-256 over the config files (company.yaml, mapping.yaml), NUL-separated; missing files are skipped."""
    h = hashlib.sha256()
    for p in paths:
        if Path(p).is_file():
            h.update(Path(p).read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def _git_sha(repo):
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "unavailable"
    except Exception:
        return "unavailable"


def _check_out(base, company_dir):
    base = Path(base).resolve()
    for forbidden in (Path(company_dir).resolve().parent, SKILL_DIR):      # companies/ and the skill folder
        if base == forbidden or forbidden in base.parents:
            raise UsageError(f"--out must be outside the companies folder and the skill folder, got {base}")
    return base


def _validate_args(as_of, lengths, end):
    if as_of is not None:
        _valid_period(as_of, "BAD_AS_OF", "as_of")
    if end not in (None, "latest"):
        _valid_period(end, "BAD_WINDOW_END", "window_end")
    if lengths is not None and (not lengths or len(set(lengths)) != len(lengths)
                                or not all(isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in lengths)):
        raise MetricsError("BAD_WINDOW_LENGTHS", f"expected distinct positive integers, got {tuple(lengths)!r}")


def _load(company, company_dir, run_dir=None, write_unmapped=True):
    """-> (records, controls, mapped). Raises AdapterError / MappingError (BLOCKED-class)."""
    records, controls = ExcelPLAdapter(company.source, company.company_id).load()
    vocab = load_vocabulary(company.categories_extra.functions, company.categories_extra.natures)
    mapping = load_mapping(company_dir, vocab)
    mapped = map_records(records, mapping, company, company_dir, out_dir=run_dir, write=write_unmapped)
    return records, controls, mapped


def check(company_dir):
    """Load + reconcile only. Writes nothing."""
    company_dir = Path(company_dir).resolve()
    company = load_company(company_dir)                                    # ConfigError -> exit 2
    try:
        records, controls, mapped = _load(company, company_dir, write_unmapped=False)
    except (AdapterError, MappingError) as e:
        return CheckResult("BLOCKED", None, e)
    recon = reconcile(records, controls, company.source, mapped=mapped)
    return CheckResult(recon.status, recon, BlockedError(recon) if recon.status != "OK" else None)


def format_recon_table(recon):
    rows = ["control | name | period | expected | actual | difference | status | source cell"]
    money = lambda x: "-" if x is None else str(x)
    for r in sorted(recon.results, key=lambda r: (r.period, r.control_id)):
        rows.append(" | ".join([r.control_id, r.control_name, r.period, money(r.expected), money(r.actual),
                                money(r.difference), "OK" if r.ok else "FAIL", r.source_ref or "-"]))
    failed = len(recon.failures)
    rows.append(f"reconciliation: {recon.status} ({len(recon.results)} controls, {failed} failed)")
    for x in recon.not_applicable:
        rows.append(f"not applicable: {x.control_id}: {x.reason}")
    return "\n".join(rows)


def _blocked(company, run_dir, exc, recon=None):
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in SUCCESS_FILES:                                             # a BLOCKED folder never holds old metrics
        (run_dir / name).unlink(missing_ok=True)
    unmapped = isinstance(exc, MappingError) and exc.code == "UNMAPPED_ACCOUNT"
    if not unmapped:
        (run_dir / "unmapped_accounts.yaml").unlink(missing_ok=True)
    failed = []
    if recon is not None:
        text = lambda x: None if x is None else str(x)
        failed = [{"control_id": r.control_id, "control_name": r.control_name, "period": r.period,
                   "expected": text(r.expected), "actual": text(r.actual), "difference": text(r.difference),
                   "source_ref": r.source_ref, "reason": r.reason} for r in recon.failures]
    flag = blocked_flag(exc)
    report = {"schema_version": 1, "status": "BLOCKED", "company_id": company.company_id, "reason": flag.observed,
              "message": flag.reason, "source_ref": getattr(exc, "source_ref", None), "failed_controls": failed,
              "flag": flag.to_dict()}
    path = run_dir / "blocked_report.json"
    write_blocked_report(path, report)
    files = [path] + ([run_dir / "unmapped_accounts.yaml"] if unmapped else [])
    return RunResult("BLOCKED", EXIT_BLOCKED, run_dir, files, flag.observed, flag.reason)


def run(company_dir, *, as_of=None, window_lengths=None, window_end=None, period=None, out_base=None):
    company_dir = Path(company_dir).resolve()
    lengths = tuple(window_lengths) if window_lengths else None
    if period is not None:
        if lengths or window_end:
            raise UsageError("--period cannot be combined with --window or --window-end")
        n, window_end = window_from_period_range(period)
        lengths = (n,)
    _validate_args(as_of, lengths, window_end)
    company = load_company(company_dir)                                    # ConfigError -> exit 2
    base = _check_out(out_base if out_base else default_out_base(company_dir), company_dir)
    root = base / company.company_id
    pre_dir = root / _run_id(as_of or "latest", lengths or company.windows.lengths, window_end or company.windows.end)

    try:
        records, controls, mapped = _load(company, company_dir, run_dir=pre_dir)
    except (AdapterError, MappingError) as e:
        return _blocked(company, pre_dir, e)
    recon = reconcile(records, controls, company.source, mapped=mapped)
    if recon.status != "OK":
        return _blocked(company, pre_dir, BlockedError(recon), recon)

    metrics = compute_metrics(mapped, recon, company, as_of=as_of, window_lengths=lengths, window_end=window_end)
    flagset = compute_flags(mapped, recon, metrics, company)
    meta = {"company_id": company.company_id, "tool_version": __version__,
            "input_sha256": _sha_file(company.source.path),
            "config_sha256": _sha_config(company_dir / "company.yaml", company_dir / "mapping.yaml"),
            "git_sha": _git_sha(company_dir.parent.parent)}
    digest = build_digest(company, metrics, flagset, mapped,
                          {"input_sha256": meta["input_sha256"], "config_sha256": meta["config_sha256"]})

    out_dir = root / _run_id(metrics.periods[-1], metrics.window_lengths, metrics.window_end)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in ("blocked_report.json", "unmapped_accounts.yaml"):
        (out_dir / stale).unlink(missing_ok=True)
    files = [out_dir / n for n in SUCCESS_FILES]
    write_digest(files[0], digest)
    write_metrics_json(files[1], meta, recon, metrics, flagset)
    write_workbook(files[2], company, metrics, flagset, recon, mapped, meta)
    return RunResult("OK", EXIT_OK, out_dir, files)
