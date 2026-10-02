# product-intelligence-agent

Continuously turn fragmented customer, competitor, and market evidence into structured,
trend-aware product signals — so a real signal doesn't stay invisible just because
nobody happened to read the right thread.

## Problem

Product teams get useful evidence from many places — Reddit threads, GitHub issues,
app-store reviews, competitor changelogs, customer interviews — but that evidence is
fragmented, repetitive, noisy, and hard to compare over time. Manually reading
everything doesn't scale, and a lot of what's there is noise: AI-generated replies,
off-topic chatter, low-effort comments that don't say anything useful.

A one-off scrape doesn't help either — the same complaint showing up in five separate
threads over two months looks like five unrelated mentions unless something is tracking
it as one recurring topic, and a real signal (a competitor sunsetting a product, a
regulatory deadline) is easy to miss entirely if it only ever shows up once.

Today this repo implements the first slice of that problem end-to-end: Reddit as a
source, and the customer-market half of the signal taxonomy (complaints, feature
requests, usability/reliability issues, pricing, switching intent — see
[Limitations](#limitations) for what's out of reach until more sources exist). The
evidence → extract → match → trend → materiality pipeline itself is source-agnostic by
design — see [Vision & roadmap](#vision--roadmap) for the fuller multi-source vision and
[the gap-analysis doc](docs/product-intelligence-agent-gap-analysis.md) for exactly
where implementation stands against it.

## Product

A weekly pipeline that:

1. **Collects** — pulls new evidence from configured sources via each source's API
   (today: Reddit, from a configured list of subreddits, via the official Reddit API).
2. **Extracts signal** — uses Claude to pull out the actual topic from each piece of
   evidence (feature request, complaint, praise, question, competitor comparison), plus
   an optional structured entity (company/product) and effective date when the source
   text names one, filtering out AI slop and irrelevant noise.
3. **Amplifies** — matches new topics against a running registry of canonical topics, so
   a subject that keeps coming back across threads, gets more comments, or attracts more
   upvotes shows up as a stronger, growing signal rather than a one-off mention.
4. **Reports** — turns the registry into a ranked view of what's trending, rising, or
   fading, as structured JSON/CSV you can feed into your own tools.
5. **Flags what matters** — scores each topic's materiality (low/medium/high/critical)
   and emits a `material_signal` event only for the ones worth a human's attention.

The result is an ongoing, low-effort read on what your product's community actually cares
about, instead of anecdotal impressions from whichever thread you happened to see.

**Status:** implemented — see [Vision & roadmap](#vision--roadmap) for what's built vs.
planned, [the gap-analysis doc](docs/product-intelligence-agent-gap-analysis.md) for a
detailed spec-vs-code comparison, and [docs/superpowers/](docs/superpowers/) for the
design specs and implementation plans behind each stage.

## Demo

Running `python run_weekly.py` against a tracked subreddit for a few weeks produces a
trend report like:

```csv
id,canonical_name,mentions_this_week,total_mentions,trend
t_a1b2c3d4,Dark mode support,5,12,rising
t_e5f6a7b8,Export to CSV,2,2,new
```

Once "Dark mode support" crosses the materiality threshold (rising trend, enough
mentions this week, high average extraction confidence), a line is appended to
`data/events.jsonl`:

```json
{
  "event": "product_intelligence.signal.material",
  "event_version": "1.0",
  "timestamp": "2026-08-22T00:00:00+00:00",
  "signal": {
    "signal_id": "SIG-...",
    "signal_type": "new_feature_demand",
    "summary": "Dark mode support: rising (trend is rising with 6 mentions this week)",
    "confidence": "high",
    "materiality": "high",
    "evidence_ids": ["EV-...", "EV-..."]
  }
}
```

Inspect any of this ad hoc without touching the database directly:

```bash
python query.py topic dark-mode-support
python query.py search "dark mode"
python query.py signal SIG-2026-0001
```

No live Reddit/Anthropic credentials are required to see this shape end-to-end — the
same trace is exercised by `tests/test_end_to_end.py` against fixture data.

## Architecture

Five independent pipeline stages chained by a single entrypoint, backed by a SQLite
database (`data/pi_agent.db`) instead of flat JSON files. Each stage is a plain Python
module and can also be run standalone.

```text
run_weekly.py
  └─> fetch.py        → evidence table
  └─> extract.py      → signal_candidate table
  └─> match.py        → canonical_topic + topic_weekly_mentions tables
  └─> report.py       → data/reports/<week>.json, data/reports/<week>.csv
  └─> materiality.py  → material_signal table, data/events.jsonl
```

- **fetch.py** — pulls new posts per subreddit via PRAW (official Reddit API wrapper),
  storing each as an immutable `evidence` row.
- **extract.py** — classifies each not-yet-processed evidence row into a `signal_type`
  (from the product intelligence signal taxonomy) plus a confidence score, storing it as
  a `signal_candidate`. Also captures an optional `entity` and `effective_date` when the
  source text names one (currently unused downstream — Reddit posts rarely name one, and
  no source that would need it, like competitor changelogs, exists yet).
- **match.py** — matches new candidates against the `canonical_topic` registry (or
  creates a new topic), incrementing per-week mention counts.
- **report.py** — computes top topics by mention count and week-over-week trend
  (new / rising / stable / falling) from the database, same output format as before.
- **materiality.py** — classifies each topic's this-week materiality (low / medium /
  high / critical) from its trend, volume, confidence, and signal type; `high`/`critical`
  topics get a `material_signal` row and a `product_intelligence.signal.material` event
  appended to `data/events.jsonl`.

Credentials (Reddit API + Anthropic API keys) are stored in the OS credential vault via
`keyring` — never in a plaintext file, never committed to the repo.

**Provenance.** Each stage adds one clearly-labeled layer on top of the last, so it's
always possible to tell what's raw, what's model output, and what's deterministic:

```text
evidence (source data, immutable)
  └─ Claude classifies  → signal_candidate (model extraction)
       └─ Claude matches → canonical_topic + weekly mentions (model extraction)
            └─ report.py       → trend (deterministic)
                 └─ materiality.py → material_signal (deterministic rule table)
                      └─ downstream: human product decision (outside this repo)
```

## Core workflows

**Signal ingestion (fetch → extract → match), per subreddit:**

```text
1. fetch.py reads state.json for the last-seen post in r/yourproduct
2. Pulls everything newer via PRAW, drops [deleted]/[removed] posts
3. Each surviving post becomes one immutable evidence row
4. extract.py sends the post's title+body to Claude, asking for a signal_type
   from a fixed enum + a one-line summary + a confidence score (or "skip")
5. match.py sends this week's new candidates + the existing canonical topic
   registry to Claude in one batch call, asking it to match or propose new topics
6. Each candidate is linked to a topic_id; topic_weekly_mentions is incremented
```

**Materiality-gated signal emission, per topic, per week:**

```text
1. report.py's trend math (deterministic) says: new / rising / stable / falling
2. materiality.py combines: signal-type impact class, trend, mentions this week,
   average extraction confidence → a materiality label (low/medium/high/critical)
3. low/medium: topic and its mentions are just recorded — no event
4. high/critical: a material_signal row is created and one line is appended to
   data/events.jsonl — this is the boundary this repo stops at (see below)
```

## AI design decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Agent architecture | No agent loop or tool-use framework — two independent, stateless LLM calls (`extract.py`, `match.py`) invoked from deterministic Python control flow | Each call has one narrow job and a fixed prompt/response contract, which is easier to test with fixture responses, trace, and reason about than a multi-step agent loop |
| Topic matching | LLM does the semantic matching against a small registry passed inline, not vector search | The registry is small and precisely keyed by slug; "is this the same topic" is a genuinely fuzzy judgment better suited to an LLM call than nearest-neighbor search over embeddings |
| Trend calculation | Deterministic weighted-average threshold logic in `report.py` | Canonical mention counts and trend direction must be reproducible and auditable — the LLM should never compute them |
| Materiality scoring | Deterministic rule table in `materiality.py` (signal-type impact class × confidence × trend × mention count) | The same signal type and evidence must always produce the same materiality label; a categorical rule table avoids the "fake mathematical precision" of an LLM-generated score |
| Writes | Fully autonomous for local storage (SQLite, JSONL); no writes ever leave the local system | The pipeline only reads Reddit and writes to its own database/files — there is no external side effect that would need human approval |
| Downstream handoff | Emit a `product_intelligence.signal.material` event and stop; don't decide what to do about it | Keeps this repo an evidence system — interpreting a signal and deciding a response belongs to the (separate, not-yet-built) Strategic Signals Agent |

See [docs/decisions/](docs/decisions/) for the fuller reasoning and alternatives
considered behind each of these.

## Safety / trust model

This pipeline has no external write path — it never posts to Reddit, calls no API with
side effects, and never auto-executes a recommendation. The human-agent boundary is
still worth stating explicitly:

**Autonomous (no human in the loop):**

- fetching new posts from configured subreddits
- classifying signal type, summary, and confidence
- matching candidates against canonical topics, or creating new ones
- computing trend and materiality
- writing to the local SQLite database and appending to `data/events.jsonl`

**Requires review before acting on it:**

- every `material_signal` this pipeline emits — `recommended_next_step` is always
  `strategic_assessment` or `urgent_strategic_assessment`, never an automated action
- newly created canonical topics — the main failure mode here is the matching LLM call
  splitting one real topic into two, or merging two distinct ones (see Limitations)

**Requires approval:** none exist in this pipeline today — there's no operation here
that writes externally or is hard to reverse.

**Blocked:** the pipeline holds only read-only Reddit API scopes and local file/DB
write access; there is nothing destructive or privileged for it to do.

## Context strategy

- **Stable, passed to every LLM call:** the fixed `signal_type` enum (`extract.py`) and
  the matching prompt's instructions (`match.py`) — these don't change per run.
- **Task-specific:** a single evidence row's title + body, sent to `extract.py` one post
  at a time.
- **Retrieved per run:** the *entire* canonical topic registry (id, name, description),
  queried fresh from SQLite and sent to `match.py` in one batch call alongside that
  week's new candidates. No pagination, no embeddings — the registry is small enough
  that structured SQL beats justifying a retrieval layer. This is the first thing to
  revisit if the registry grows past a few hundred topics (see Limitations).
- **Deliberately excluded:** author/username is never stored or sent to the model —
  only title, body, score, and comment count survive `fetch.py`.
- **Staleness:** there is no caching layer; every stage reads the current database state
  directly, so there's nothing to invalidate.

## Evaluation

What exists today: 1,500+ lines of deterministic unit and end-to-end tests
(`tests/`) covering fetch error handling (rate limits, auth failures, server errors),
trend math, materiality thresholds, and a full fetch→extract→match→report→materiality
trace — all against fixture Reddit/Anthropic responses, so the suite runs without any
live API calls or credentials.

What doesn't exist yet: a live-LLM evaluation harness. Nothing currently measures
signal-type extraction accuracy, topic-matching false-merge rate, or materiality
precision against a labeled dataset — only that the deterministic logic downstream of
the LLM behaves correctly given a known model response. This is a real gap, not an
oversight — see [Limitations](#limitations) and [Vision & roadmap](#vision--roadmap).

## Observability

- Every LLM call is exactly one `client.messages.create(...)` with a fixed prompt
  template (`build_extraction_prompt` / `build_matching_prompt`) — no hidden multi-turn
  state to lose track of.
- Each stage (`fetch`/`extract`/`match`/`materiality`) opens one DB connection, does all
  its writes, and either commits once at the end or rolls back entirely on any
  exception — a failed run never leaves a partial write behind.
- Material signal emission is append-only: one JSON line per event in
  `data/events.jsonl`, never rewritten or deleted.
- Ad hoc inspection during development: `python query.py topic <slug>`,
  `python query.py search <keyword>`, `python query.py signal <signal_id>` (see
  [Demo](#demo) for sample output).

Sensitive values (API keys, Reddit usernames) never appear in the database, the event
log, or query output — they're excluded at `fetch.py`'s ingestion boundary, not redacted
after the fact.

## Setup

1. Install dependencies: `pip install -r requirements.txt`
2. Create a Reddit "script"-type app at reddit.com/prefs/apps to get a client ID and secret.
3. Copy `config.yaml.example` to `config.yaml` and list the subreddits to track.
4. Store credentials in your OS credential vault (one-time): `python set_credentials.py`
   — you'll be prompted for `reddit_client_id`, `reddit_client_secret`, `reddit_user_agent`
   (format: `platform:app-id:version (by /u/your-username)`), and `anthropic_api_key`.
5. Run the full weekly pipeline: `python run_weekly.py`
6. Run a single stage (e.g. after fixing a bug in matching, without re-fetching):
   `python run_weekly.py --stage match`

For a fully detailed, click-by-click walkthrough (creating the Reddit/Anthropic API
keys, saving them into Windows Credential Manager, verifying each step, and
troubleshooting), see [SETUP.md](SETUP.md).

## Development

Install dev dependencies and run the test suite:

```bash
pip install -r requirements-dev.txt
pytest -v
```

## Limitations

- **Extraction/matching quality is unmeasured.** No evaluation harness scores
  signal-type accuracy, false-merge rate, or materiality precision against labeled
  examples — see [Evaluation](#evaluation).
- **Topic matching doesn't scale past a few hundred topics.** `match.py` sends the
  *entire* canonical registry to the LLM on every run; there's no batching or retrieval
  layer yet (see [Context strategy](#context-strategy)).
- **Single source.** Only Reddit is implemented. GitHub Issues, competitor changelogs,
  and customer-interview summaries from the wider vision are not built.
- **Only the customer-market signal taxonomy is reachable.** `extract.py`'s
  `SIGNAL_TYPES` covers 12 of the ~50 signal types the full vision defines (complaints,
  feature requests, usability/reliability issues, pricing, switching intent, and
  similar). The Competitive/Technology/Regulatory/Ecosystem taxonomies exist only as
  dead constants in `materiality.py` (`EVENT_DRIVEN_HIGH_SIGNAL_TYPES`,
  `EVENT_DRIVEN_CRITICAL_SIGNAL_TYPES`) — no source can produce a candidate with those
  types until a competitor-changelog, regulatory-feed, or similar source is added. The
  `entity`/`effective_date` fields on `signal_candidate` were built for this case ahead
  of the source that needs them, and sit unused today.
- **Trend engine implements 4 of 7 states.** `report.py` detects `new` / `rising` /
  `stable` / `falling`; the vision's `sharply_rising`, `resurfacing`, and `dormant`
  states aren't implemented.
- **No scheduled or event-driven execution.** The pipeline only runs when manually
  invoked (`run_weekly.py`, or an OS task scheduler calling it) — there's no built-in
  scheduler or webhook-triggered mode.
- **No Integration API.** Downstream consumers (like a Strategic Signals Agent) would
  need `get_signal`/`get_evidence`/`search_related_signals`/`get_topic_trend` tools; today
  the only access path is `query.py` or direct SQLite reads.
- **No natural-language query interface.** Ad hoc questions ("what's rising fastest?")
  require hand-written SQL via `query.py`, not a conversational interface.
- **Single-tenant.** One `config.yaml`, one SQLite file, one product's subreddits per
  deployment — not designed for multiple products or teams sharing one instance.

## Vision & roadmap

This repo implements a slice of a larger product-intelligence-agent vision — see
[docs/product-intelligence-agent-vision.md](docs/product-intelligence-agent-vision.md).

**Implemented:** Reddit ingestion, customer-market signal extraction (with optional
entity/effective-date capture), canonical topic matching, deterministic trend detection,
and materiality-gated event emission.

**Next, in rough priority order** (matches the phased roadmap in
[the gap-analysis doc](docs/product-intelligence-agent-gap-analysis.md)):

- **Evaluation harness** (signal-type accuracy, entity/date extraction accuracy,
  topic-matching false-merge rate, materiality precision against labeled examples). Why:
  confidence in classification quality currently rests on test fixtures with
  known-correct responses, not measured accuracy against real, ambiguous input — the
  biggest unverified assumption in the system.
- **Additional Phase-1 sources** (GitHub Issues, competitor changelogs,
  customer-interview summaries). Why: the vision's Phase-1 scope is deliberately
  multi-source; Reddit alone under-represents structured issue feedback and competitor
  intelligence, and can't reach the Competitive/Technology/Regulatory/Ecosystem signal
  types described in [Limitations](#limitations) — a competitor-changelog source is also
  what would actually exercise the `entity`/`effective_date` fields already in the
  schema. Design spec for the first of these (GitHub Issues):
  [docs/superpowers/specs/2026-09-06-github-issues-source-design.md](docs/superpowers/specs/2026-09-06-github-issues-source-design.md).
- **Trend engine completeness** (`sharply_rising`, `resurfacing`, `dormant`, on top of
  the 4 states `report.py` already computes). Why: small and purely deterministic, but
  needed before trend output matches the vision's full state machine.
- **Integration API / MCP toolset** (`get_signal`, `get_evidence`,
  `search_related_signals`, `get_topic_trend`, `search_feedback`). Why: a Strategic
  Signals Agent needs a way to retrieve full evidence without depending on this repo's
  storage internals.
- **Scheduled/event-driven launch modes.** Why: a real deployment needs the pipeline
  running on its own cadence, not only when someone remembers to invoke it.
- **Natural-language query interface.** Why: source-backed answers to ad hoc questions
  are currently only reachable by hand-writing SQL via `query.py`; likely close to free
  once the Integration API/MCP toolset exists.

## Reddit API compliance

Built on the official Reddit API (via PRAW), not HTML scraping — only configured public
subreddits, minimal data retention (no author/username stored), and rate limiting
handled by PRAW's built-in throttling. See the design spec for details.

## License

See [LICENSE](LICENSE).


## Evals

`evals/` holds a golden-set eval suite for the two stages that ask Claude to make judgment
calls: extraction (`extract.py`) and topic matching (`match.py`). The runners call the real
production functions against the real Anthropic API. They are **not** run by pytest or CI.

```powershell
python evals/run_extraction_eval.py [--filter database_development] [--model claude-sonnet-5] [--no-save]
python evals/run_matching_eval.py   [--filter connection_pooling_duplicate] [--model claude-sonnet-5] [--no-save]
```

- The API key comes from the OS credential store, the same way the pipeline gets it (`set_credentials.py`).
- The extraction eval exact-matches `skip`, `signal_type`, `entity` (only the keys the golden file names, case-insensitive), and `effective_date`. It grades `summary` with an LLM judge (`evals/judge.py`, `claude-opus-5`). Each non-skipped example costs two API calls.
- The matching eval checks each candidate's decision: it must match the named existing topic, or create a complete new topic when the golden file says `matched_topic_id: null`.
- Results are classified as `passed`, `failed` (a model mistake, including unusable output), or `errored` (the API call failed, so the example is excluded from accuracy). The exit code is 0 whenever a report is printed. It is 2 for malformed golden YAML, a `--filter` that matches nothing, or missing credentials.
- Golden data lives in `evals/golden/extraction/<product_area>.yaml` (many examples per file) and `evals/golden/matching/<scenario>.yaml` (one scenario per file). See the design spec at `docs/superpowers/specs/2026-09-24-extraction-matching-evals-design.md` for the format. `tests/test_evals_golden.py` validates the committed golden files in CI.

### Run history and drift

Every runner saves its run to `evals/results/<kind>-<UTC timestamp>.json` (gitignored) unless you pass `--no-save`. A run file records the models, a fingerprint of each prompt, a fingerprint of the golden data it graded, the `--filter`, and the git commit, next to every per-example result.

```powershell
python evals/compare_runs.py --kind extraction          # latest extraction run vs the one before it
python evals/compare_runs.py evals/results/extraction-20260929T101500Z.json   # pinned baseline vs latest
python evals/compare_runs.py BASELINE.json CURRENT.json
```

The comparison prints pass-rate and per-field deltas, examples that regressed (passed -> failed, with the reason) or were fixed, and examples present in only one run. When the model, a prompt, the golden data or the filter differs between the two runs, it prints a warning block first. Read the deltas as "the model changed its behavior" only when there are no warnings.

### Judge calibration

The summary judge is itself measured against human labels in `evals/golden/judge/*.yaml`. Each case holds evidence, a reference summary, a candidate summary, and `human_verdict: pass|fail`.

```powershell
python evals/export_judge_cases.py evals/results/extraction-<stamp>.json   # draft unlabeled cases from a real run
python evals/run_judge_eval.py [--filter tuning|holdout] [--model MODEL]
```

"Passed" in a judge run means the judge agreed with the human. The report adds false passes (judge accepted a summary the human rejected) and false fails. Label draft cases yourself before looking at what the judge said, add hand-written failing variants so the set contains real fails, and keep `holdout.yaml` out of sight while editing the rubric. Cases with `human_verdict: null` are rejected at load time.

### Tuning the judge rubric

Follow Task 6 of `docs/superpowers/plans/2026-09-29-eval-run-history-and-judge-tuning-implementation.md`: baseline agreement on `tuning` and `holdout`, edit `JUDGE_PROMPT_TEMPLATE` in `evals/judge.py` against the disagreements on `tuning` only, and keep a change only if it does not lose agreement on `holdout` and does not add false passes. After a rubric change, the next extraction comparison will warn `judge prompt changed`. That is expected: summary pass-rate movement across that boundary is the judge, not the extractor.
