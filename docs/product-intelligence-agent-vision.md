# Product Intelligence Agent

## Purpose

The Product Intelligence Agent is the sensing and analysis layer of a broader AI-native product intelligence system.

Its job is to continuously collect fragmented market and customer evidence, normalize it, identify meaningful changes, and produce structured, evidence-backed signals.

It should **not** decide what the company should do.

Instead, it should answer:

- What changed?
- Is the change real?
- How strong is the signal?
- Which product, customer segment, competitor, or market area does it affect?
- Is the signal growing, fading, new, or recurring?
- What evidence supports it?
- Is it potentially material enough to trigger deeper strategic investigation?

Its main downstream consumer is the **Strategic Signals Agent**, which takes selected material signals and determines whether they represent a threat, opportunity, strategic watch item, or no-action event.

---

# 1. Product Positioning

## Short positioning statement

**An AI product-intelligence system that continuously turns fragmented customer, competitor, and market evidence into structured, traceable signals for product teams.**

## Core distinction

The Product Intelligence Agent is responsible for:

```text
OBSERVE
  ↓
NORMALIZE
  ↓
UNDERSTAND
  ↓
DETECT CHANGE
  ↓
PRODUCE SIGNAL
```

The Strategic Signals Agent is responsible for:

```text
SIGNAL
  ↓
VERIFY
  ↓
ASSESS RELEVANCE
  ↓
INVESTIGATE
  ↓
THREAT / OPPORTUNITY / WATCH
  ↓
RECOMMENDED RESPONSE
  ↓
FOLLOW-UP
```

This separation is intentional.

The Product Intelligence Agent should remain an evidence system.

The Strategic Signals Agent should remain a decision-preparation and response-management system.

---

# 2. Product Problem

Product teams receive useful evidence from many places:

- Reddit
- GitHub Issues
- app-store reviews
- support tickets
- customer interviews
- sales notes
- competitor release notes
- competitor pricing pages
- competitor documentation
- product announcements
- public roadmaps
- changelogs
- community forums
- industry news
- regulatory announcements
- ecosystem/platform announcements

The problem is not lack of information.

The problem is that useful signals are:

- fragmented
- repetitive
- noisy
- difficult to compare over time
- difficult to connect across sources
- easy to miss
- often noticed only after they become obvious

The Product Intelligence Agent converts raw observations into persistent, comparable product signals.

---

# 3. Scope

## Phase 1 sources

Start with a deliberately small set:

1. Reddit
2. GitHub Issues
3. competitor release notes / changelogs
4. manually supplied customer-interview summaries

These sources are enough to demonstrate:

- public feedback
- structured issue feedback
- competitor intelligence
- qualitative customer evidence

## Later sources

Possible extensions:

- support-ticket exports
- G2 / Capterra / app-store reviews
- competitor pricing pages
- RSS/news sources
- product documentation
- public status pages
- regulatory feeds
- public company announcements
- community forums
- internal win/loss data using synthetic examples

---

# 4. Data Model

## 4.1 Raw evidence object

Every source becomes a normalized evidence record.

```json
{
  "evidence_id": "EV-2026-000184",
  "source_type": "competitor_changelog",
  "source_name": "Competitor X",
  "source_url": "https://example.com/changelog",
  "captured_at": "2026-08-23T14:20:00Z",
  "published_at": "2026-08-22T09:00:00Z",
  "title": "Product Y support ending in May 2027",
  "content": "...",
  "metadata": {
    "competitor": "Competitor X",
    "product": "Product Y"
  }
}
```

Raw evidence should remain immutable.

Any interpretation must be stored separately.

---

## 4.2 Extracted signal candidate

The model converts evidence into one or more candidate signals.

```json
{
  "candidate_id": "SC-2026-00420",
  "evidence_ids": ["EV-2026-000184"],
  "signal_type": "product_end_of_support",
  "entity": {
    "type": "competitor_product",
    "company": "Competitor X",
    "product": "Product Y"
  },
  "summary": "Competitor X announced end of support for Product Y.",
  "effective_date": "2027-05-31",
  "confidence": 0.98
}
```

The candidate is not yet a strategic signal.

---

# 5. Signal Taxonomy

Signals should be classified into useful product-level categories.

## 5.1 Customer-market signals

- complaint rising
- complaint falling
- new feature demand
- new use case
- usability issue
- reliability issue
- pricing complaint
- switching intent
- competitor mention rising
- customer migration intent
- churn-related issue
- positive adoption pattern

## 5.2 Competitive signals

- product launch
- feature launch
- major feature improvement
- feature removal
- product end-of-life
- product end-of-support
- pricing change
- packaging change
- free-tier change
- acquisition
- partnership
- major customer win
- API change
- licensing change
- distribution change

## 5.3 Technology signals

- new model capability
- new AI API capability
- open-source release
- framework deprecation
- major platform feature
- ecosystem standard change

## 5.4 Regulatory signals

- regulation announced
- regulation approved
- implementation deadline
- compliance requirement change

## 5.5 Ecosystem signals

- platform policy change
- marketplace policy change
- vendor integration change
- infrastructure pricing change
- cloud/platform capability launch

---

# 6. Canonical Topic Registry

Repeated observations should accumulate against persistent canonical topics.

Example:

```text
"schema browser is slow"
"object explorer freezes on large DB"
"loading 20k tables takes forever"
```

Canonical topic:

```text
slow-schema-loading
```

The canonical topic registry should contain:

```json
{
  "topic_id": "TOPIC-0014",
  "slug": "slow-schema-loading",
  "name": "Slow schema loading",
  "description": "Users report poor performance when loading large database schemas.",
  "aliases": [
    "object explorer slow",
    "schema browser performance"
  ],
  "first_seen": "2026-06-04",
  "last_seen": "2026-08-22"
}
```

This makes trends durable across time.

---

# 7. Trend Engine

The system should calculate trends deterministically.

The LLM may classify meaning.

It should not calculate canonical trend counts.

Example:

```json
{
  "topic_id": "TOPIC-0014",
  "period": "2026-W34",
  "mentions": 29,
  "previous_period_mentions": 13,
  "change": 1.23,
  "trend": "rising"
}
```

Possible trend states:

- new
- rising
- sharply_rising
- stable
- falling
- resurfacing
- dormant

---

# 8. Material Signal Generation

Not every observation should trigger the Strategic Signals Agent.

The Product Intelligence Agent should create a **material signal** only when a threshold is met.

## 8.1 Material signal object

```json
{
  "signal_id": "SIG-2026-0182",
  "created_at": "2026-08-23T14:32:00Z",
  "signal_type": "product_end_of_support",
  "entity": {
    "company": "Competitor X",
    "product": "Product Y"
  },
  "summary": "Competitor X will end support for Product Y on 2027-05-31.",
  "confidence": "high",
  "evidence_ids": [
    "EV-2026-000184",
    "EV-2026-000191"
  ],
  "change_type": "new_event",
  "materiality": {
    "score": 0.86,
    "reason": [
      "confirmed by primary source",
      "affects known competitor product",
      "contains explicit future deadline"
    ]
  },
  "recommended_next_step": "strategic_assessment"
}
```

---

# 9. Materiality Rules

Materiality should combine deterministic rules with AI interpretation.

Possible factors:

```text
confidence
× relevance
× magnitude
× novelty
× urgency
× persistence
```

Do not expose the result as fake mathematical precision.

The system can use categorical reasoning:

```text
LOW
MEDIUM
HIGH
CRITICAL
```

## Examples

### Low

One Reddit complaint with little engagement.

Action:

```text
store only
```

### Medium

Recurring complaint rising gradually.

Action:

```text
update topic
```

### High

Competitor announces a major pricing change.

Action:

```text
emit strategic signal
```

### Critical

Regulatory deadline affecting a core workflow.

Action:

```text
emit immediate strategic signal
```

---

# 10. Trigger Contract with Strategic Signals Agent

The two repositories should communicate through an explicit event contract.

## Event name

```text
product_intelligence.signal.material
```

## Example payload

```json
{
  "event": "product_intelligence.signal.material",
  "event_version": "1.0",
  "timestamp": "2026-08-23T14:32:00Z",
  "signal": {
    "signal_id": "SIG-2026-0182",
    "signal_type": "product_end_of_support",
    "summary": "Competitor X will end support for Product Y.",
    "confidence": "high",
    "materiality": "high",
    "effective_date": "2027-05-31",
    "evidence_ids": [
      "EV-2026-000184",
      "EV-2026-000191"
    ]
  }
}
```

The Strategic Signals Agent receives the signal ID and retrieves full evidence through a tool/API.

Do not duplicate all raw evidence inside the event.

---

# 11. Integration API

Product Intelligence can expose a small API or MCP toolset.

## `get_signal`

```text
get_signal(signal_id)
```

Returns the full material signal.

## `get_evidence`

```text
get_evidence(evidence_ids[])
```

Returns source-backed evidence.

## `search_related_signals`

```text
search_related_signals(entity, topic, period)
```

## `get_topic_trend`

```text
get_topic_trend(topic_id, period)
```

## `search_feedback`

```text
search_feedback(query, filters)
```

These tools allow the Strategic Signals Agent to investigate without sharing storage internals.

---

# 12. Launch Modes

The Product Intelligence Agent should support several ways of running.

## 12.1 Scheduled ingestion

Example:

```text
every 6 hours:
  competitor sources

daily:
  Reddit / GitHub Issues

weekly:
  long-horizon trend recomputation
```

## 12.2 Manual run

CLI:

```bash
python run.py ingest --source reddit
python run.py ingest --source competitor-x
python run.py analyze --since 2026-08-20
```

## 12.3 Single-source investigation

```bash
python run.py analyze-url https://competitor.com/changelog
```

Useful for testing.

## 12.4 Event-driven mode

A source change can invoke ingestion immediately.

Example:

```text
RSS webhook
  ↓
collector
  ↓
signal extraction
```

---

# 13. Product Intelligence Agent Interface

Natural-language queries should remain available.

Examples:

> What changed in competitor pricing this month?

> Which complaints are rising fastest?

> What new signals appeared after version 4.2?

> Which competitors are increasingly mentioned by users?

> Show evidence behind the slow-schema-loading trend.

The agent should answer with source-backed evidence.

---

# 14. Evaluation

## 14.1 Signal extraction

Measure:

- signal type accuracy
- entity extraction accuracy
- effective-date extraction
- confidence calibration

## 14.2 Topic matching

Measure:

- false merge rate
- duplicate topic rate
- topic matching precision

## 14.3 Trend detection

Use synthetic time series with known expected results.

## 14.4 Materiality

Create labeled examples:

```text
archive
track
emit strategic signal
emit urgent strategic signal
```

Measure:

- missed material signal rate
- false escalation rate

## 14.5 Evidence quality

Measure:

- source relevance
- primary-source preference
- citation correctness

---

# 15. Demo Scenario

A strong demo should show the downstream integration.

## Step 1

The Product Intelligence Agent collects a competitor changelog.

## Step 2

It extracts:

```text
Competitor X will end support for Product Y on May 31, 2027.
```

## Step 3

It verifies the statement from a second public source.

## Step 4

It creates:

```text
SIG-2026-0182
```

## Step 5

Materiality rules classify it as:

```text
HIGH
```

## Step 6

The system emits:

```text
product_intelligence.signal.material
```

## Step 7

The Strategic Signals Agent receives it and begins its own lifecycle.

The Product Intelligence Agent stops there.

This boundary should be explicit in both repositories.

---

# 16. What This Project Should Signal

A reviewer should conclude that the project demonstrates:

- LLM extraction
- multi-source normalization
- deterministic analytics
- semantic matching
- evidence provenance
- time-series signal detection
- agent tools
- event contracts
- product-oriented AI system design
- clear separation between intelligence and strategic response

---

# 17. Avoid

Do not turn Product Intelligence into:

- automatic roadmap prioritization
- generic sentiment analysis
- word clouds
- simple summarization
- untraceable AI scoring
- strategic recommendation engine

Its job is to produce trustworthy intelligence and material signals.
