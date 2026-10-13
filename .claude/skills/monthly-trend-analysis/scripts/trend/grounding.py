"""The grounding checker: verifies a DRAFT commentary against digest.json. Deterministic code, never a model.

The draft and the digest are untrusted text. They are only ever parsed with regular expressions and json: nothing
in them is evaluated, imported or executed, and a token is just an id looked up in a dictionary.

Checks (codes are stable; word lists and limits are in reference/commentary_rules.yaml):
  G1  UNKNOWN_TOKEN         a {{id}} that is not in the digest's allowed_facts
  G2  BARE_NUMBER           digits, '%' or '$' outside tokens (allowed: years that appear in the digest's periods,
                            and window lengths such as "12-month"; month names are fine)
  G3  CAUSAL_CLAIM          causal phrasing is banned outright (PLAN 6.2 rule 5): no hedge excuses it. The tool
                            describes movements, not causes; reword as a question for the analyst
  G4  DIRECTION_MISMATCH    deliberately narrow: only sentences with exactly ONE signed token are judged
  G5  MISSING_COVERAGE      as-of and high-severity flag entries, headline rows, reported AND adjusted figures for
                            adjusted periods (and the reported AND adjusted operating income of every configured
                            window that contains an adjusted period), and the digest's WARNING line must be cited.
                            An entry counts as cited when
                            a token contains one of its flag ids, or is an evidence id that belongs to that entry alone
  G6  MISSING_DRAFT_BANNER  the first line must say the text is a DRAFT that is not reviewed
  G7  MALFORMED_TOKEN       unbalanced or malformed braces
  G8  BANNED_WORD, G9 BODY_TOO_LONG, G10 MISSING_SECTION   PLAN 6.2 rules 5 and 4
"""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .facts import parse_fact_id
from .metrics import shift

RULES_FILE = Path(__file__).resolve().parents[2] / "reference" / "commentary_rules.yaml"
CHECKS = {
    "G1": ("UNKNOWN_TOKEN", "A {{token}} must be an id listed in the digest's allowed_facts."),
    "G2": ("BARE_NUMBER", "Numbers may only appear inside {{tokens}} (years from the digest and window lengths excepted)."),
    "G3": ("CAUSAL_CLAIM", "Causal phrasing is banned outright. This tool describes movements, not causes."),
    "G4": ("DIRECTION_MISMATCH", "A direction word must agree with the sign of the one signed value in its sentence."),
    "G5": ("MISSING_COVERAGE", "The draft must cover the flagged items, headline rows and adjusted figures in the digest."),
    "G6": ("MISSING_DRAFT_BANNER", "The first line must say the text is a DRAFT that has not been reviewed."),
    "G7": ("MALFORMED_TOKEN", "Tokens look like {{fact.id}}: balanced braces, no spaces."),
    "G8": ("BANNED_WORD", "These words are banned in commentary (PLAN 6.2 rule 5)."),
    "G9": ("BODY_TOO_LONG", "The body (before Data notes) has a word limit (PLAN 6.2 rule 4)."),
    "G10": ("MISSING_SECTION", "The commentary needs the PLAN 6.2 sections, and few Headline bullets."),
}
NOTES = {
    "G3": "Causal phrasing is banned outright (PLAN 6.2 rule 5). This tool describes movements, not causes, and a hedge "
          "such as 'may' does not excuse it.",
    "G4": "This check is deliberately narrow: only sentences with exactly one signed token are judged, and sentences "
          "that contain both an up word and a down word are skipped. It will miss many contradictions.",
}
_ORDER = {c: i for i, c in enumerate(CHECKS)}
_TOKEN_SPAN = re.compile(r"\{\{.*?\}\}")
_TOKEN_OK = re.compile(r"\{\{([A-Za-z0-9_.\-]+)\}\}")
_STRAY = re.compile(r"[\w.\-]*[{}][\w.\-{}]*")
_TOK, _BAD = "", ""
_MOVEMENT_FIELDS = {"abs", "pct", "pp", "change_pct"}


@dataclass(frozen=True)
class Failure:
    code: str
    message: str
    line: object = None          # 1-based, or None for a whole-draft finding
    text: str = ""

    @property
    def name(self):
        return CHECKS[self.code][0]

    def to_dict(self):
        return {"code": self.code, "name": self.name, "message": self.message, "line": self.line, "text": self.text}


def load_rules(path=RULES_FILE):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def report_notes(rules=None):
    """The explanatory notes that go into grounding_report.json."""
    rules = rules or load_rules()
    return {**NOTES, "G3": NOTES["G3"] + " " + rules["causal_advice"]}


# ---------------------------------------------------------------- scanning

def _scan(line):
    """-> (tokens, bad, plain, blanked). tokens: [(start, id)]; bad: [(start, text)] malformed fragments.
    plain: the line with every token / fragment replaced by one marker character (for sentence logic).
    blanked: same length as the line, tokens and fragments replaced by spaces (for digit scanning)."""
    tokens, bad, spans = [], [], []
    for m in _TOKEN_SPAN.finditer(line):
        ok = _TOKEN_OK.fullmatch(m.group())
        if ok:
            tokens.append((m.start(), ok.group(1)))
            spans.append((m.start(), m.end(), _TOK))
        else:
            bad.append((m.start(), m.group()))
            spans.append((m.start(), m.end(), _BAD))
    work = list(line)
    for a, b, _ in spans:
        work[a:b] = [" "] * (b - a)
    rest = "".join(work)
    for m in _STRAY.finditer(rest):
        bad.append((m.start(), m.group()))
        spans.append((m.start(), m.end(), _BAD))
    spans.sort()
    plain, blanked, pos = [], list(line), 0
    for a, b, mark in spans:
        plain.append(line[pos:a])
        plain.append(mark)
        blanked[a:b] = [" "] * (b - a)
        pos = b
    plain.append(line[pos:])
    return tokens, sorted(bad), "".join(plain), "".join(blanked)


def tokens_in(text):
    """Valid token ids in order of appearance (malformed ones are ignored)."""
    return [i for line in text.replace("\r\n", "\n").split("\n") for _, i in _scan(line)[0]]


def _numeric(display):
    s = display.replace(",", "").replace("%", "").replace("pp", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _is_movement(fid):
    if fid.startswith("flag."):
        return fid.rsplit(".", 1)[-1] in _MOVEMENT_FIELDS
    try:
        p = parse_fact_id(fid)
    except ValueError:
        return False
    return p["basis"] == "delta" or "mom_" in p["view"] or "prev_" in p["view"]


def _phrase_re(words):
    alt = "|".join(sorted((re.escape(w).replace(r"\ ", r"\s+") for w in words), key=len, reverse=True))
    return re.compile(rf"\b(?:{alt})\b", re.IGNORECASE)


def _digest_facts(digest):
    allowed = digest.get("allowed_facts") if isinstance(digest, dict) else None
    if not isinstance(allowed, dict):
        raise ValueError("not a digest: allowed_facts is missing")
    return allowed


def _years(digest):
    periods = [digest.get(k) for k in ("as_of", "first_period", "prior_period", "window_end")]
    periods += [(digest.get("headline") or {}).get(k) for k in ("period", "prior_period")]
    periods += [a.get("period") for a in digest.get("adjusted_periods", [])]
    periods += [w.get("end") for w in digest.get("windows", [])]
    periods += [e.get("period") for e in (digest.get("flags") or {}).get("top", [])]
    return {p[:4] for p in periods if isinstance(p, str) and re.match(r"\d{4}", p)}


# ---------------------------------------------------------------- the checks

def check_draft(draft, digest, rules=None):
    """-> list[Failure], sorted by line then code. Empty list = grounded."""
    rules = rules or load_rules()
    allowed = _digest_facts(digest)
    lines = draft.replace("\r\n", "\n").split("\n")
    scans = [_scan(ln) for ln in lines]
    out = []
    years = _years(digest)
    lengths = {int(n) for n in digest.get("window_lengths", [])}
    causal = _phrase_re(rules["causal_phrases"])
    banned = _phrase_re(rules["banned_words"])
    up, down = _phrase_re(rules["direction_words"]["up"]), _phrase_re(rules["direction_words"]["down"])
    cited = set()
    banner_line = next((n for n, ln in enumerate(lines, start=1) if ln.strip()), None)

    for n, (line, (tokens, bad, plain, blanked)) in enumerate(zip(lines, scans), start=1):
        for _, text in bad:                                                              # G7
            out.append(Failure("G7", "Malformed token: expected {{fact.id}} with balanced braces and no spaces.", n, text))
        for _, fid in tokens:                                                            # G1
            if fid in allowed:
                cited.add(fid)
            else:
                out.append(Failure("G1", f"Token {{{{{fid}}}}} is not in the digest's allowed facts.", n, "{{" + fid + "}}"))

        scan = blanked                                                                   # G2
        if n == banner_line:
            scan = re.sub(r"\([0-9a-f]{8}\)", lambda m: " " * len(m.group()), scan)       # PLAN banner: "(<sha8>)"
        scan = re.sub(r"(?<!\d)(\d{4})(?!\d)", lambda m: "Y" * 4 if m.group(1) in years else m.group(1), scan)
        scan = re.sub(r"(?<!\d)(\d+)(?=[- ]months?\b)", lambda m: "X" * len(m.group(1)) if int(m.group(1)) in lengths else m.group(1), scan)
        for m in re.finditer(r"[\d%$][\d,.%]*", scan):
            out.append(Failure("G2", "Bare number outside a token (digits, %, $). Quote numbers only as {{fact id}}.",
                               n, line[m.start():m.end()]))

        ids = iter(i for _, i in tokens)                                                 # sentence-level checks
        for sent in re.split(r"(?<=[.!?])\s+", plain):
            if not sent.strip():
                continue
            sent_ids = [next(ids, None) for _ in range(sent.count(_TOK))]
            words = sent.replace(_TOK, " ").replace(_BAD, " ")
            shown = sent.strip()
            for k in sent_ids:
                shown = shown.replace(_TOK, "{{" + str(k) + "}}", 1)
            shown = shown.replace(_BAD, "{{?}}")

            found = [m.group().lower() for m in causal.finditer(words)]                  # G3: no hedge exception
            if found:
                named = " and ".join(f"'{p}'" for p in dict.fromkeys(found))
                out.append(Failure("G3", f"Causal phrasing ({named}) is not allowed, even with a hedge: this tool describes "
                                         f"movements, not causes. {rules['causal_advice']}", n, shown))

            for m in banned.finditer(words):                                             # G8
                out.append(Failure("G8", f"Banned word '{m.group()}'.", n, m.group()))

            signed = []                                                                  # G4
            for fid in sent_ids:
                disp = allowed.get(fid) if fid else None
                v = _numeric(disp) if isinstance(disp, str) else None
                if v is None or v == 0:
                    continue
                if v < 0 or _is_movement(fid):
                    signed.append(v)
            if len(signed) == 1:
                has_up, has_down = up.search(words), down.search(words)
                if signed[0] < 0 and has_up and not has_down:
                    out.append(Failure("G4", f"'{has_up.group()}' contradicts the negative value in this sentence.", n, shown))
                elif signed[0] > 0 and has_down and not has_up:
                    out.append(Failure("G4", f"'{has_down.group()}' contradicts the positive movement in this sentence.", n, shown))

    out += _banner(lines, rules)
    out += _sections_and_length(lines, scans, rules)
    out += _coverage(digest, cited)
    out.sort(key=lambda f: (f.line is None, f.line or 0, _ORDER[f.code], f.text))
    return out


def _banner(lines, rules):
    for n, ln in enumerate(lines, start=1):
        if ln.strip():
            ok = ln.strip().startswith(rules["banner_start"]) and any(p in ln.lower() for p in rules["banner_phrases"])
            if ok:
                return []
            return [Failure("G6", "The first line must start with 'DRAFT' and say it is not reviewed / for analyst review.", n, ln.strip())]
    return [Failure("G6", "The draft is empty: it needs a DRAFT banner as its first line.", 1, "")]


def _headings(lines):
    out = []
    for n, ln in enumerate(lines, start=1):
        m = re.match(r"^\s{0,3}#{1,6}\s*(.+?)\s*:?\s*#*\s*$", ln)
        if m:
            out.append((n, m.group(1).strip().lower()))
    return out


def _sections_and_length(lines, scans, rules):
    out, heads = [], _headings(lines)
    have = {h for _, h in heads}
    for need in rules["required_sections"]:
        if need.lower() not in have:
            out.append(Failure("G10", f"Missing section heading '{need}'.", None, need))
    first = next((n for n, ln in enumerate(lines) if ln.strip()), None)
    end = next((n - 1 for n, h in heads if h == rules["data_notes_heading"].lower()), len(lines))
    body = [scans[i][2] for i in range((first or 0) + 1, end)]
    words = [w for w in " ".join(body).replace(_BAD, _TOK).split() if re.search(r"\w|" + _TOK, w)]
    if len(words) > rules["max_body_words"]:
        out.append(Failure("G9", f"The body has {len(words)} words; the limit is {rules['max_body_words']} "
                                 "(tokens count as one word; Data notes are not counted).", None, f"{len(words)} words"))
    hl = next((n for n, h in heads if h == "headline"), None)
    if hl is not None:
        nxt = next((n for n, _ in heads if n > hl), len(lines) + 1)
        bullets = [n for n in range(hl + 1, nxt) if re.match(r"^\s*([-*]|\d+[.)])\s+", lines[n - 1])]
        if len(bullets) > rules["max_headline_bullets"]:
            out.append(Failure("G10", f"Headline has {len(bullets)} bullets; the limit is {rules['max_headline_bullets']}.",
                               bullets[rules["max_headline_bullets"]], lines[bullets[rules["max_headline_bullets"]] - 1].strip()))
    return out


def _coverage(digest, cited):
    out = []
    flags = digest.get("flags") or {}
    top = flags.get("top", [])
    owners = {}                                   # evidence id -> how many entries list it
    for e in top:
        for i in {i for d in e.get("details", []) for i in d.get("evidence", [])}:
            owners[i] = owners.get(i, 0) + 1
    for e in top:
        if e.get("group") not in ("as_of", "high_severity"):
            continue
        flag_ids = [d["flag_id"] for d in e.get("details", [])]
        # an evidence id proves coverage only if it belongs to this entry alone (rep.opinc.2025-12 is shared by
        # both Dec 2025 items, so citing it must not count as citing either of them)
        evidence = {i for d in e.get("details", []) for i in d.get("evidence", []) if owners[i] == 1}
        if not any(any(fid in c for fid in flag_ids) or c in evidence for c in cited):
            what = f"{e.get('subject_label', e.get('subject'))} | {e.get('period')} | {'+'.join(e.get('types', []))}"
            out.append(Failure("G5", f"The draft does not cite the {e['group']} flag entry: {what}. Cite one of its "
                                     f"numbers, e.g. {{{{{next(iter(e['details'][0]['facts'].values()), flag_ids[0])}}}}}.", None, what))
    rows = {r["label"]: r for r in (digest.get("headline") or {}).get("rows", [])}
    for label in load_rules()["coverage_headline_rows"]:
        r = rows.get(label)
        if r and r["rep"].get("value") not in cited:
            out.append(Failure("G5", f"The draft does not cite the headline row '{label}': use {{{{{r['rep']['value']}}}}}.",
                               None, label))
    row_label = load_rules()["coverage_adjusted_row"]
    for a in digest.get("adjusted_periods", []):
        row = next((r for r in a.get("month", []) if r["label"] == row_label), None)
        if row:
            for basis, key in (("reported", "rep"), ("adjusted", "adj")):
                if row[key] not in cited:
                    out.append(Failure("G5", f"The draft does not cite the {basis} {row_label} figure for {a['period']}: "
                                             f"use {{{{{row[key]}}}}}.", None, f"{row_label} {a['period']} {basis}"))
    adjusted = [a["period"] for a in digest.get("adjusted_periods", [])]
    for w in digest.get("windows", []):                     # only a window that holds an adjusted period needs the pair
        try:
            first = shift(w["end"], -(int(w["length"]) - 1))
        except (KeyError, ValueError, TypeError):
            continue
        inside = [p for p in adjusted if first <= p <= w["end"]]
        row = next((r for r in w.get("rows", []) if r.get("label") == row_label), None)
        if not inside or row is None:
            continue
        for basis, key in (("reported", "rep"), ("adjusted", "adj")):
            cell = row.get(key)
            fid = cell.get("value") if isinstance(cell, dict) else None      # "same_as_reported" has no separate figure
            if fid and fid not in cited:
                out.append(Failure(
                    "G5", f"The draft does not cite the {basis} {row_label} figure for the {w['length']}-month window "
                          f"ending {w['end']}. The window contains the adjusted period {', '.join(inside)}, so cite both the "
                          f"reported and the adjusted figure: use {{{{{fid}}}}}.", None,
                    f"{row_label} {w['length']}-month window ending {w['end']} {basis}"))
    for note in digest.get("data_notes", []):
        if note.startswith("WARNING"):
            need = re.findall(r"\{\{([^{}]+)\}\}", note)
            if not set(need) <= cited:
                out.append(Failure("G5", "The digest carries a WARNING in data_notes that the draft does not repeat: "
                                         "cite " + ", ".join("{{" + i + "}}" for i in need) + " in a line that starts with WARNING.",
                                   None, note))
    return out
