---
name: monthly-trend-analysis
description: Produce a monthly trend pack and DRAFT commentary from a company P&L workbook. P0 stub; not yet functional.
---

# Monthly trend analysis (P0 stub)

Nothing here is functional yet. This file exists so the skill folder and the
`${CLAUDE_SKILL_DIR}` path can be checked.

If the user's message asks for the "skill-dir probe": first quote the probe
command line below character for character, as you see it, then run it with
the shell tool and show the raw output. Do nothing else.

Probe command:

```powershell
python "${CLAUDE_SKILL_DIR}/scripts/trend_cli.py" --help
```

Repo-relative fallback, run from the repo root:

```powershell
python .claude/skills/monthly-trend-analysis/scripts/trend_cli.py --help
```
