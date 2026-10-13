"""run_manifest.json: what `run` wrote, so `render` can tell whether digest.json was changed afterwards.

Deterministic (no timestamps, no absolute paths, fixed key order). The manifest is an integrity check against accidental
or careless edits by the commentary writer; it is not a signature, because anything that can write the run folder can
rewrite the manifest too.
"""
import hashlib
import json
from pathlib import Path

MANIFEST_NAME = "run_manifest.json"
SCHEMA_VERSION = 1

# integrity failure codes (reported like grounding codes, same exit code 4)
INTEGRITY = {
    "DIGEST_TAMPERED": "digest.json differs from what `run` wrote, or the folder is not the run the manifest describes.",
    "DIGEST_UNVERIFIED": "run_manifest.json is missing or unreadable, so digest.json cannot be checked.",
}


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def build_manifest(run_id, digest_bytes, metrics_bytes):
    return {"schema_version": SCHEMA_VERSION, "run_id": run_id,
            "files": {"digest.json": {"sha256": sha256_bytes(digest_bytes)},
                      "metrics.json": {"sha256": sha256_bytes(metrics_bytes)}}}


def manifest_text(manifest):
    return json.dumps(manifest, indent=1, ensure_ascii=False) + "\n"


def write_manifest(run_dir):
    """Hash the digest.json and metrics.json now in `run_dir` and write run_manifest.json beside them."""
    run_dir = Path(run_dir)
    m = build_manifest(run_dir.name, (run_dir / "digest.json").read_bytes(), (run_dir / "metrics.json").read_bytes())
    path = run_dir / MANIFEST_NAME
    path.write_text(manifest_text(m), encoding="utf-8", newline="\n")
    return path


def verify_digest(run_dir):
    """None if digest.json matches the manifest, else (code, message, expected_sha, actual_sha)."""
    run_dir = Path(run_dir)
    actual = sha256_bytes((run_dir / "digest.json").read_bytes())
    path = run_dir / MANIFEST_NAME
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
        expected, run_id = m["files"]["digest.json"]["sha256"], m["run_id"]
        if not (isinstance(expected, str) and isinstance(run_id, str)):
            raise TypeError
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        why = "is missing" if not path.is_file() else "cannot be read as a manifest"
        return ("DIGEST_UNVERIFIED",
                f"{MANIFEST_NAME} {why}, so digest.json cannot be checked against what `run` wrote. "
                "Run `run` again to regenerate the run folder, then render.", None, actual)
    if actual != expected:
        return ("DIGEST_TAMPERED",
                f"digest.json does not match {MANIFEST_NAME}: `run` wrote sha256 {expected}, the file now has {actual}. "
                "The digest was changed after `run` wrote it. Run `run` again to regenerate it; never edit the digest.",
                expected, actual)
    if run_id != run_dir.name:
        return ("DIGEST_TAMPERED",
                f"{MANIFEST_NAME} describes run {run_id!r} but this folder is {run_dir.name!r}: digest.json was not "
                "written for this run. Run `run` again.", expected, actual)
    return None
