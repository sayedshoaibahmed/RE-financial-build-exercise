# Architecture

Labels: BUILT (in the repo), Tested (a test or eval exercises it), PROPOSED (design for ten clients, not built), Not tested.

## 1. How the project is structured (files, skills, memory)

```
connector\                      Task 1: read-only QuickBooks MCP server, own venv and tests
.claude\settings.json           permission deny rules
.claude\skills\monthly-trend-analysis\
  SKILL.md                      the instructions Claude follows
  scripts\trend\                Python package: all arithmetic, checks, grounding
  reference\  templates\        rules, glossaries, company and mapping templates
companies\abc_inc\              company.yaml, mapping.yaml (human-approved)
data\                           the ABC Inc workbook
tests\  evals\                  unit tests, oracle, golden cases, mutation harness
outputs\<company>\<run>\        digest.json, run_manifest.json, pack, commentary
```

The skill is the one instruction layer; there is no CLAUDE.md (BUILT). The Python package does every calculation and writes `digest.json`; Claude only writes a hedged DRAFT commentary quoting numbers as digest fact tokens, and `render` grounding-checks it (BUILT, Tested).

"Memory" is files only: per company, `company.yaml` and `mapping.yaml`; per run, `digest.json` plus `run_manifest.json`. Nothing is remembered between runs except those files (BUILT). This is deliberate: all state is explicit files that can be diffed and audited. Claude's own cross-session memory is not used for client facts, because it could blur the boundary between clients.

PROPOSED: at ten clients, each tenant folder gets a short, versioned client-notes file for what a human reviewer learned (for example "this client's Marketing row is a parent account"), read by the skill, never written by Claude without review.

## 2. Hosting

The connection choice, rejected alternatives and reasoning are in `connector\README.md` (Alternatives considered, Security design and the "DESIGN NOT BUILT" section).

Today everything runs on my laptop: the connector under Claude Desktop (stdio), the skill under Claude Code (BUILT). Sandbox development keys only; the app keys (`.env`) and the tokens are both in `%LOCALAPPDATA%\qbo-mcp`, outside the repo; the secret scan keeps secrets out of the repo. Nothing is deployed.

PROPOSED team hosting: the MCP server and the skill package run centrally; secrets live in a managed secret store, not files; read-only stays enforced in code (GET only and a path allowlist, both tested offline today), because the Intuit scope allows writes; each user has an identity and every tool call goes to an audit log. Reasoning: a plaintext token file protected only by Windows user permissions suits one person and one sandbox, not a team; a central server gives one place to rotate secrets and see who read what. Trade-off: a central server is a shared target and needs operations work (uptime, access control, token refresh coordination).

## 3. Ten clients

PROPOSED: shared code (skill package and connector); one tenant per client, each with its own config folder, its own QuickBooks connection and token store, its own output folder and its own process. No shared token, no shared output path, no cross-client read.

BUILT toward this: per-company folders under `companies\`; `company.yaml` and `mapping.yaml` are protected from Claude's edits by deny rules, and each run records their hash in the digest, which the manifest guards; outputs go to `outputs\<company>\`. Not built: there is one connector, one sandbox and one token folder. Not tested: running more than one real company.

What breaks first, ranked:

1. Writing and reviewing `mapping.yaml` for each new company is human time (BUILT as a human gate).
2. A company that uses the same account label under two paths is BLOCKED with AMBIGUOUS_ACCOUNT (found by reading `mapping.py`, not run end to end).
3. Ten refresh tokens, each with its own roughly 100-day expiry, to track (PROPOSED problem, Not tested).
4. The QuickBooks adapter exists at record level but is not wired into the CLI, so the skill runs on Excel only (BUILT, Tested at record level).
5. Evals cover one real company and two synthetic ones (G06, G09) (Tested).

## 4. Where it stands (honest limits)

- Permissions are a second layer, not a sandbox: deny rules do not bind subprocesses. The Read and Edit denies were tested live; the Edit rule also blocked the Write tool.
- The manifest guards careless edits only, not deliberate ones.
- Chat answers are guided by an instruction in `SKILL.md`, not checked by code.
- The Intuit accounting scope allows writes; read-only is enforced by our code and tests, not by the scope.
- Live prose quality was reviewed once by a model judge (manual, ungated, `evals\l3\`), not by the suite.

