# Mapping workflow: unmapped accounts and new companies

Every account must be classified twice before a run can proceed: by **function** (who spends) and by **nature** (what the
money is spent on). Income and cost-of-goods accounts need no function or nature. The allowed values are in
`categories.md` (`categories.yaml`); a company can add its own only through `categories_extra:` in its `company.yaml`.

## Who does what
| Step | Who |
|---|---|
| Create `companies/<id>/company.yaml` from `templates/company.template.yaml` | a human |
| List the accounts (`list-accounts`) | the skill |
| Write `companies/<id>/mapping.proposed.yaml` (proposals only) | the skill |
| Review the proposal, create `companies/<id>/mapping.yaml`, set `approved_by` and `approved_on` | a human |
| Re-run | the skill |

The skill never creates or edits `mapping.yaml` or `company.yaml`. The pipeline only ever reads `mapping.yaml`; it ignores
`mapping.proposed.yaml`. A human approval is the only thing that makes a mapping count.

## Writing the proposal
1. Run `list-accounts --company <id>`. Every account is listed with its section and path, and with status UNMAPPED.
   (After a BLOCKED run with UNMAPPED_ACCOUNT, `unmapped_accounts.yaml` in the run folder lists the missing ones.)
2. Write `mapping.proposed.yaml`. Start the file with the line
   `# PROPOSED — NOT USED BY THE PIPELINE — needs human approval`, then one entry per account:
   - opex accounts: `account`, `section: opex`, `function`, `nature`
   - income and cogs accounts: `account`, `section: income` or `cogs` (optionally `group`)
   - every entry also carries the proposal-only keys `rationale`, `confidence` (high / medium / low) and `proposed: true`,
     plus `approved_by: null` and `approved_on: null`
   - `expected_negative: true` only for lines that are normally negative (for example interest income)
3. Use only the ids in `categories.md`. Base each proposal on the account name and its path, nothing else. Do not open the
   source workbook. Say which proposals are low confidence.
4. Tell the person: a human must review the proposal, copy the approved entries into `mapping.yaml`, delete `rationale`,
   `confidence` and `proposed`, and fill `approved_by` and `approved_on`. The loader rejects an entry that still has a
   proposal key or no approver (UNAPPROVED_MAPPING), an unknown category (UNKNOWN_CATEGORY) and an unmapped account
   (UNMAPPED_ACCOUNT).

## Not supported yet
Only an Excel P&L with the `excel_pl` layout can be read. If the source is anything else (QuickBooks, a PDF, a different
Excel layout), say it is not supported yet and stop. Do not try to read it yourself.
