# Decision

Compute trend direction (`report.py`) and materiality labels (`materiality.py`) with
deterministic Python logic, never by asking the LLM for a score.

## Context

Two things need to be calculated every week per topic: is mention volume rising,
falling, new, or stable, and how materially important is that change. Both could be
phrased as a question to the LLM ("does this topic look like it's rising? how important
is this?").

## Options considered

- **Ask the LLM to judge trend and importance directly**, feeding it this week's and
  prior weeks' mention counts. Rejected: the same input would not reliably produce the
  same output across calls, and there would be no way to audit *why* a topic crossed
  the materiality threshold beyond "the model said so."
- **A single opaque numeric score** (e.g. a weighted formula collapsed to one float).
  Rejected: the vision doc explicitly warns against "fake mathematical precision" —
  a bare 0.83 score implies more certainty than the underlying signal supports.
- **Deterministic rules over categorical labels** (chosen): `report.py`'s
  `_trend_direction()` compares this week's mentions against a recency-weighted average
  with fixed thresholds (`RISING_THRESHOLD = 1.2`, `FALLING_THRESHOLD = 0.8`);
  `materiality.py`'s `classify_materiality()` is an explicit rule table over signal-type
  impact class, trend, mention count, and average extraction confidence, producing one
  of four labels (`low`/`medium`/`high`/`critical`) plus a list of plain-language
  `reasons`.

## Decision

Trend and materiality are both computed by fixed, inspectable Python logic. The LLM's
only inputs to this calculation are the `signal_type` and `confidence` it already
produced during extraction — it never scores trend or materiality itself.

## Consequences

- Given the same database state, `compute_trends()` and `classify_materiality()` always
  return the same result — this is what makes `tests/test_report.py` and
  `tests/test_materiality.py` able to assert exact trend/label values rather than
  ranges.
- Every `material_signal` row carries a human-readable `materiality_reasons` list (e.g.
  `"trend is rising with 6 mentions this week"`) instead of an unexplained score — a
  reviewer can check the reasoning against the raw mention counts in
  `topic_weekly_mentions` directly.
- Threshold constants (`RISING_THRESHOLD`, `HIGH_TREND_MENTIONS_THRESHOLD`, etc.) are
  currently hand-picked rather than tuned against labeled outcomes — this is the gap the
  evaluation harness in the README's Roadmap is meant to close.
