"""JSON outputs: metrics.json, digest.json, blocked_report.json. Deterministic: no timestamps, fixed key order."""
import json
from pathlib import Path

from ..facts import parse_fact_id, to_text, unit_of


def dumps(obj):
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


def write_json(path, obj):
    Path(path).write_text(dumps(obj), encoding="utf-8", newline="\n")


def _money(v):
    return None if v is None else str(v)


def recon_dict(recon):
    return {"status": recon.status,
            "results": [{"control_id": r.control_id, "control_name": r.control_name, "period": r.period,
                         "expected": _money(r.expected), "actual": _money(r.actual),
                         "difference": _money(r.difference), "source_ref": r.source_ref, "ok": r.ok,
                         "reason": r.reason} for r in recon.results],
            "not_applicable": [{"control_id": x.control_id, "reason": x.reason} for x in recon.not_applicable]}


def metrics_document(meta, recon, metrics, flagset):
    """Everything the run computed, machine-readable. Full fact table: id -> text (null = n/a, see `na`)."""
    facts, na = {}, {}
    for fid, v in metrics.facts.items():
        text = to_text(v, unit_of(parse_fact_id(fid)))
        facts[fid] = text
        if text is None:
            na[fid] = v.reason + (": " + ", ".join(v.missing) if v.missing else "")
    return {"schema_version": 1, "status": "OK", "meta": meta,
            "run": {"as_of": metrics.periods[-1], "as_of_requested": metrics.as_of,
                    "periods": list(metrics.periods), "window_lengths": list(metrics.window_lengths),
                    "window_end": metrics.window_end},
            "reconciliation": recon_dict(recon),
            "adjustments": [{"account": a.account, "period": a.period, "reported": str(a.reported_amount),
                             "adjusted": str(a.adjusted_amount), "delta": str(a.delta), "present": a.present,
                             "reason": a.reason} for a in metrics.adjustments],
            "flags": flagset.to_dict(), "facts": facts, "na": na}


def write_metrics_json(path, meta, recon, metrics, flagset):
    write_json(path, metrics_document(meta, recon, metrics, flagset))


def write_blocked_report(path, report):
    write_json(path, report)


def write_digest(path, digest):
    write_json(path, digest)
