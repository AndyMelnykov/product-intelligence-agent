# Decision

Optional Claude Haiku 5.5 triage before extraction (`extraction_cascade`). Triage may only skip, trusts a skip only at confidence >= TRIAGE_SKIP_THRESHOLD, and fails open. A deterministic 5% of trusted skips is still extracted, as a production audit.

The cascade ships off. Whether to turn it on is decided by the rule below, from measurements that have not been taken yet.

## Context

Every post cost one Sonnet 5 call with adaptive thinking, obvious spam included. Haiku 5.5 is about 20x cheaper per token ($0.10 / $0.50 vs $2 / $10 per MTok). The risk is a false skip: a real signal the extractor never sees, which no later stage can recover.

## Options considered

- **Sonnet at lower effort** (measured first, per the Claude API guidance on cascades): one model and one prompt, no new failure mode.
- **Haiku triage** (this ADR): a second narrow call, consistent with ADR 001. It only removes work, never adds a classification.
- **Batch API** (50% off): orthogonal, but needs asynchronous collection. Deferred.

## Measurements

Not yet measured. Fill this table from `python evals/run_extraction_eval.py [--effort medium|low]` and `python evals/run_triage_eval.py`, then compare runs with `python evals/compare_runs.py`.

| Option | skip acc. | signal_type acc. | false skips @ threshold | tokens / post (in, out) | est. $ / 1000 posts |
| --- | --- | --- | --- | --- | --- |
| Sonnet, default effort | | | n/a | | |
| Sonnet, effort low | | | n/a | | |
| Sonnet, effort medium | | | n/a | | |
| Haiku triage @ 0.90 + Sonnet default | | | | | |

Decision rule:
- Enable the cascade only if the chosen threshold has **zero false skips** on the golden set, including `prompt_injection.yaml` once P5 adds it.
- And it must save more than the best Sonnet-effort option at equal `skip` and `signal_type` accuracy.

Outcome: not enabled, because the measurements above have not been run. Update this line with the result.

## Consequences

- With the cascade on, `python query.py triage-audit` reports the false-skip rate on the audit sample. Check it after four weekly runs; any false skip means raise the threshold or turn the cascade off.
- Changing `TRIAGE_SKIP_THRESHOLD` or the prompt is a reviewed code change (CLAUDE.md: prompt contracts are human-designed).
