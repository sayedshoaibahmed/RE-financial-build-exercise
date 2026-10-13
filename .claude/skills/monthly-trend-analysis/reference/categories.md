# Category vocabulary

Every operating-expense account is classified twice, using the ids below:

- **function** (who spends): answers "which part of the business is this cost for?"
- **nature** (what the money is spent on): answers "what kind of cost is it?"

Both groupings cover every opex account exactly once, so each must add up to Total Operating
Expenses (reconciliation controls C9a and C9b). Income and COGS accounts need no function or nature.

The machine-readable source of truth is `categories.yaml` (the loader reads that file; this page is
its human-readable twin). Mappings use the `id` in backticks, never the label.

A company may add categories only through an explicit `categories_extra:` block in `company.yaml`.
Anything else is rejected with `UNKNOWN_CATEGORY`. This default set is a sensible starting point, not
the firm's standard (assumption A19); if the firm has its own chart, replace `categories.yaml` and
re-approve the mappings.

## Functions

| id | Label | Meaning |
|---|---|---|
| `r_and_d` | R&D | Product and engineering people and contractors, with their payroll taxes, benefits and workers' comp. |
| `sales_marketing` | Sales & Marketing | Sales, marketing and partnerships people and contractors, advertising, events, partner commissions and S&M travel. |
| `operations` | Operations | Operating (delivery/support) payroll with its taxes, benefits and workers' comp. |
| `people_unallocated` | People (unallocated) | Company-wide people costs not attributable to one function (bonus, health insurance, recruiting, training, severance); there is no headcount data to allocate them. |
| `technology` | Technology | Software, SaaS and AI/compute vendor costs that run the business. |
| `g_and_a` | G&A | Professional fees, rent, insurance, office, banking and merchant fees, bad debts, depreciation, general travel and meals. |
| `non_operating_tax` | Non-operating & tax | Interest income and expense and income taxes. The workbook shows these inside operating expenses; they are not reclassified. |
| `stock_based_comp` | Stock-based compensation | Non-cash equity compensation expense. |

## Natures

| id | Label | Meaning |
|---|---|---|
| `payroll_benefits` | Payroll & benefits | All payroll, payroll-tax, benefits and workers' comp lines across functions, plus bonus, health insurance and severance. |
| `contractors_professional_fees` | Contractors & professional fees | Contractors and outside professional services (accounting, legal, advisory, consulting). |
| `marketing_events` | Marketing & events | Advertising, partner marketing, events and conferences. |
| `software_technology` | Software & technology | Software and SaaS subscriptions, AI token cost, telephone and internet. |
| `travel_entertainment` | Travel & entertainment | Travel, meals and entertainment. |
| `facilities_office` | Facilities & office | Rent, office supplies, postage and printing. |
| `commissions` | Commissions | Sales and partner commissions. |
| `other_operating` | Other operating | Costs that fit no other nature: bank and merchant fees, bad debts, insurance, charities, training, recruiting, payroll service fees, general expenses. |
| `depreciation` | Depreciation | Depreciation of fixed assets. |
| `stock_based_comp` | Stock-based compensation | Non-cash equity compensation expense. |
| `interest_tax` | Interest & tax | Interest income and expense and income taxes. |
