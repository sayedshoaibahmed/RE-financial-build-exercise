# L3 judge rubric: commentary quality (manual, ungated)

You are judging one DRAFT monthly trend commentary for a small company's profit and loss.
The draft is written for the company's owner and for the analyst who reviews it.

## What you are given

- The draft. Numbers in it are written as tokens such as `{{rep.rev.total.2026-06}}`.
- The digest the draft was written from (JSON). Its `allowed_facts` maps every valid token id to its value and
  display string. Its `flags` list the movements the tool marked as notable, with severity. `adjusted_periods`,
  `conventions` and `data_notes` explain the reported and adjusted bases.

To read a token, look up its id in `allowed_facts`. A token that is NOT in `allowed_facts` is an unsupported
number. A plain number typed into the text (not a token) is also unsupported. Judge only against the digest; do
not use outside knowledge of the company.

## Scoring

Score each of the six criteria from 1 to 5. Use the anchors for 1, 3 and 5; 2 and 4 sit between them.
Judge each criterion on its own: a weakness under one criterion must not lower the score of another, unless the
same sentence fails both.

### (a) Hedging

Is the certainty of each statement right for what the digest supports? Is the draft clearly marked as a draft for
review?

- 5: Facts are stated plainly; anything that needs confirmation (for example whether an item is non-recurring) is
  framed as to be confirmed. No predictions. The draft is clearly labelled as an unreviewed draft.
- 3: Mostly well calibrated, with one lapse: one statement more certain than the digest supports, one prediction,
  or a missing or unclear draft label.
- 1: Speculation or forecasts stated as fact (for example "will recover"), or hedging so heavy the facts are lost,
  and no draft label.

### (b) Materiality order

Are the largest and highest-severity items first, and is every high or medium severity flag in the digest covered?

- 5: Leads with the headline results (revenue, margins, operating income); the order follows size and severity;
  every high and medium flag is mentioned.
- 3: Broadly sensible order, but either one material flag is missing or a minor item is placed ahead of a clearly
  larger one.
- 1: Minor items lead, or several high or medium flags are missing.

### (c) Owner relevance

Would an owner understand what happened to the business this month, in plain words, without noise?

- 5: Plain language; covers revenue, margins, costs and results; every number cited is one an owner would care
  about and is a valid token.
- 3: Useful overall, but with jargon, clutter, a meaningless or unsupported figure, or a gap in one core area.
- 1: Hard to follow, mostly noise, or misses what matters to an owner.

### (d) No invented causes

Does the draft avoid saying WHY something happened, beyond what the digest facts support? The digest records
movements, not causes. Describing a movement is fine. Naming a possible explanation as a question for the
reviewer is fine.

- 5: No cause is asserted anywhere. Movements are described; explanations appear only as questions or items to
  confirm.
- 3: One mild causal suggestion that is not framed as a question (for example "driven by" with no support).
- 1: One or more causes stated as fact (for example "because of ..."), or a direction of movement that
  contradicts the digest.

### (e) Basis handling

The digest has a reported basis and an adjusted basis. Dec 2025 has configured items excluded on the adjusted
basis. Judge whether:
(1) Dec 2025 (and any window that contains it) is flagged as affected;
(2) results are shown both with and without the flagged items (reported AND adjusted), each time the adjusted
    basis is mentioned;
(3) when excluding the items makes results WORSE, the draft says so plainly (or at least does not imply the
    exclusion improves results).
If the digest has no adjusted periods, score on whether the draft correctly says there are no adjustments and does
not invent any.

- 5: All three met (or, with no adjustments, correctly stated).
- 3: Partly met: for example the reported and adjusted pair is given once but missing elsewhere, or the effect of
  the exclusion is not stated.
- 1: Adjusted figures without reported ones (or the reverse) for the affected period, or the exclusion is
  presented as improving results when it worsens them.

### (f) Reviewer questions are useful

Do the questions for the reviewer point at things a person can check and that the digest cannot answer?

- 5: Two or more specific, checkable questions tied to flagged items, with no padding or repetition.
- 3: Questions exist but are generic, or one is useful and the rest are padding.
- 1: No questions, or questions that are repetitive filler, or that ask the reviewer to do the tool's job.

## Output format

Return JSON only, no prose before or after:

```json
{"draft_id": "D01",
 "scores": {"a": 0, "b": 0, "c": 0, "d": 0, "e": 0, "f": 0},
 "evidence": {"a": "one sentence", "b": "one sentence", "c": "one sentence",
              "d": "one sentence", "e": "one sentence", "f": "one sentence"}}
```

Each evidence sentence must point at the draft text (quote a few words) or at a digest fact id.
