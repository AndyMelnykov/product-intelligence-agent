# Spec vs. Implementation: Gap Analysis and Roadmap

**Date:** 2026-09-16
**Compares:** the "Product Intelligence Agent" north-star spec (saved locally as
[docs/product-intelligence-agent-vision.md](product-intelligence-agent-vision.md)) against the
code in this repository as of commit `7fef019`.

This document is a snapshot, not a living spec. As phases below are implemented, update or retire
the corresponding row rather than letting this file drift out of sync with the code.

## Summary

The repository is a working, single-source, end-to-end pipeline — not pre-MVP scaffolding, but
well short of the full vision. Reddit → LLM classification (`extract.py`) → LLM topic-matching
(`match.py`) → deterministic trend (`report.py`) → deterministic materiality (`materiality.py`) →
event emission (`materiality.py::emit_event`) all work end-to-end with 87 passing tests. The
project's own documentation (README, all 4 ADRs) is scrupulously honest about what's built vs.
planned — this analysis is "spec vs. code," not "docs vs. code."

## Gaps by spec section

| # | Spec section | Gap |
|---|---|---|
| 1 | §4.2 Signal candidate | [signal_candidate](../db.py) has no `entity` (`{type, company, product}`) or `effective_date` field. The spec's flagship example — "Competitor X ends support for Product Y on 2027-05-31" — has nowhere to be stored today. |
| 2 | §3 Sources | Only Reddit is implemented ([fetch.py](../fetch.py)). GitHub Issues has a full design spec ([docs/superpowers/specs/2026-09-06-github-issues-source-design.md](superpowers/specs/2026-09-06-github-issues-source-design.md)) but zero code. Competitor changelogs and customer-interview summaries aren't even spec'd. |
| 3 | §5 Signal taxonomy | Only 12 of ~50 types are reachable (§5.1 Customer-market, in [extract.py](../extract.py)'s `SIGNAL_TYPES`). The §5.2–§5.5 taxonomies (Competitive/Technology/Regulatory/Ecosystem) exist only as dead constants in [materiality.py](../materiality.py) (`EVENT_DRIVEN_HIGH_SIGNAL_TYPES`, `EVENT_DRIVEN_CRITICAL_SIGNAL_TYPES`) — no source can ever produce a candidate with those types. |
| 4 | §7 Trend engine | [report.py](../report.py) implements 4 of the spec's 7 trend states (`new`/`rising`/`stable`/`falling`). Missing: `sharply_rising`, `resurfacing`, `dormant`. |
| 5 | §11 Integration API | Not implemented. No `get_signal`, `get_evidence`, `search_related_signals`, `get_topic_trend`, `search_feedback` — as an API or MCP tool. [query.py](../query.py) is an ad hoc read-only SQL CLI, explicitly documented in the README as not filling this role. |
| 6 | §12 Launch modes | No `run.py` with `ingest --source` / `analyze --since` / `analyze-url`. Only [run_weekly.py](../run_weekly.py) `--stage {fetch,extract,match,report,materiality,all}`. No scheduled or event-driven mode. |
| 7 | §13 NL query interface | Not implemented — SQL only, via `query.py`. |
| 8 | §14 Evaluation | No labeled-example eval harness (signal-type accuracy, entity/date extraction, topic false-merge rate, materiality precision). The `tests/` suite (87 tests) validates deterministic logic against *fixed* LLM fixtures — it doesn't measure real classification accuracy. README's own "Limitations" section names this as the top open item. |
| 9 | §10 Trigger contract | Producer side implemented (`materiality.py::emit_event` writes `product_intelligence.signal.material` to `data/events.jsonl`, per ADR 004). No consumer exists anywhere — the event contract is currently one-way. |

Not treated as a gap: multi-tenancy / multiple configs and DBs. The spec doesn't call for it, and
it isn't needed for a single-product demo.

## Proposed phased roadmap

Ordered by dependency (what unblocks what), not strictly by spec section number.

1. **Phase 0 — `signal_candidate` schema prerequisite.** Add `entity` and `effective_date` to
   `signal_candidate` and the extraction path. Small, but blocks Phase 3 — there's currently
   nowhere to put a competitor/product entity or a deadline date.
   → detailed plan: [docs/superpowers/plans/2026-09-16-signal-candidate-entity-effective-date-implementation.md](superpowers/plans/2026-09-16-signal-candidate-entity-effective-date-implementation.md)

2. **Phase 1 — Evaluation harness.** Labeled examples + metrics for signal-type accuracy,
   entity/date extraction, topic false-merge rate, materiality precision. README already names
   this the top priority; without it, trusting any of the expansion phases below is guesswork.

3. **Phase 2 — GitHub Issues source.** The design spec already exists
   ([docs/superpowers/specs/2026-09-06-github-issues-source-design.md](superpowers/specs/2026-09-06-github-issues-source-design.md));
   this phase is pure implementation: `fetch_github.py`, config, credential, a wording tweak to
   `extract.py`'s prompt. Low risk — reuses the existing customer-market taxonomy unchanged.

4. **Phase 3 — Competitor changelog source** (needs Phase 0). The only phase that actually
   exercises the §5.2 Competitive taxonomy sitting dead in `materiality.py` today. Needs its own
   design spec first (same shape as the GitHub Issues one), then a fetcher, entity/date
   extraction, and an `extract.py` prompt extension to classify against competitive signal types.

5. **Phase 4 — Trend engine completeness.** Add `sharply_rising`, `resurfacing`, `dormant` to
   `report.py`. Small, purely deterministic.

6. **Phase 5 — Integration API / MCP toolset.** `get_signal`, `get_evidence`,
   `search_related_signals`, `get_topic_trend`, `search_feedback`, wrapping `db.py`/`query.py`.
   Worth doing once there's enough signal diversity (Phases 2–3) to make the toolset worth
   querying.

7. **Phase 6 — Launch modes.** `run.py` with `ingest --source` / `analyze --since` /
   `analyze-url`, then a formalized scheduled/event-driven mode (today: "configure Task Scheduler
   yourself").

8. **Phase 7 — NL query interface.** Likely close to free once Phase 5 exists as an MCP server —
   any MCP client (Claude Code/Desktop) already gives NL access to those tools. Treat as a
   probable side effect of Phase 5, not a separate build.

Not phased separately: manually supplied customer-interview summaries (§3). It's a cheap addition
(a manual text-to-`evidence` ingestion path, no external API) — fold it into whichever early phase
has spare room rather than giving it its own slot.
