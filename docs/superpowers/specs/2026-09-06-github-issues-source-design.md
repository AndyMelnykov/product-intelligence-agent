# GitHub Issues Source — Design

## Purpose

The product intelligence vision (see the "Product Intelligence Agent" north-star
doc, saved at [docs/product-intelligence-agent-vision.md](../../product-intelligence-agent-vision.md),
§3) lists four Phase-1 sources: Reddit, GitHub Issues, competitor release
notes/changelogs, and manually supplied customer-interview summaries. Today
only Reddit is implemented. With only one source, several downstream vision-doc
capabilities can never actually fire: multi-source verification (§15 step 3),
the event-driven materiality path for non-customer-market signal types (§5.2-
§5.5), and any meaningful entity beyond `customer_topic`.

This sub-project adds the second Phase-1 source: **GitHub Issues on the
product's own repo(s)**. It reuses the existing customer-market signal
taxonomy and pipeline unchanged — the new work is purely in evidence
acquisition (`fetch_github.py`), plus one wording change downstream.

## Non-goals

- No competitor-repo tracking. This only tracks issues on the product's own
  repo(s) — the same "what are users asking for/complaining about" framing as
  Reddit, just from a more technical audience. Competitor repos would need
  the §5.2 competitive taxonomy and general entity extraction, which is a
  separate, later sub-project.
- No new signal types and no entity extraction. Issue/comment content is
  classified with the same 12 customer-market `SIGNAL_TYPES` `extract.py`
  already uses; `material_signal.entity` stays `customer_topic`-shaped.
- No multi-source verification logic yet. This sub-project only makes a
  second source *exist*; teaching `materiality.py` to notice when a topic is
  corroborated across sources is separate follow-up work.
- No pull requests. GitHub's issues API includes PRs in the same endpoint;
  those are filtered out.
- No per-comment or per-issue reaction/vote weighting — comments and issues
  are evidence rows like any other; volume/trend math is unchanged.
- No new dependency beyond PyGithub (no `requests`-based hand-rolled client).

## Architecture

```text
run_weekly.py
  └─> fetch.py         → evidence table (source_type="reddit_post")
  └─> fetch_github.py  → evidence table (source_type="github_issue" | "github_issue_comment")
  └─> extract.py       → signal_candidate table (unchanged, now source-agnostic wording)
  └─> match.py         → canonical_topic + topic_weekly_mentions tables (unchanged)
  └─> report.py        → data/reports/<week>.json, data/reports/<week>.csv (unchanged)
  └─> materiality.py   → material_signal table, data/events.jsonl (unchanged)
```

`extract.py`, `match.py`, `report.py`, and `materiality.py` already operate
generically over the `evidence`/`signal_candidate` tables regardless of
`source_type` — no changes needed to any of them beyond the wording tweak
described below. All new work is in `fetch_github.py`, plus small additions
to `config.py`, `credentials.py`, `db.py`, and `run_weekly.py`.

### fetch_github.py

New module, structurally parallel to `fetch.py`, using PyGithub as the client
(mirrors PRAW's role for Reddit — object model plus built-in pagination and
rate-limit handling).

For each configured repo, per run:

1. Read the repo's cursor from `state.json` (via the existing `state.py`
   `load_state`/`save_state` — no new state file), keyed
   `f"github:{owner}/{repo}"`. No cursor yet → treat as epoch.
2. List open issues via `repo.get_issues(state="open", since=cursor)`, capped
   at `fetch_limit_per_repo`. GitHub's `since` filter means "updated at or
   after," so a single per-repo cursor naturally catches both brand-new
   issues and existing open issues that picked up new comments — there is no
   need to track per-issue comment cursors separately.
3. For each returned issue:
   - Build its evidence candidate (`source_type="github_issue"`, `source_url`
     = the issue's HTML URL, `published_at` = issue `created_at`, `title` =
     issue title, `content` = issue body, `metadata` = `{issue_number, state,
     labels, comments_count}`). Insert it **only if** no evidence row already
     exists for that `source_url`.
   - List its comments via `issue.get_comments(since=cursor)`. For each
     comment, build an evidence candidate (`source_type="github_issue_comment"`,
     `source_url` = the comment's HTML URL, `published_at` = comment
     `created_at`, `title` = the issue's title, `content` = comment body,
     `metadata` = `{issue_number, comment_id}`), inserted under the same
     dedup rule.
4. On full success, advance the repo's cursor in `state.json` to the run's
   wall-clock start time and commit the DB transaction.

**Dedup instead of cursor-boundary math**: rather than deciding "is this
issue new this run vs. did it just get a new comment" from timestamps, every
candidate insert is guarded by a new `db.get_evidence_by_source_url` check.
This makes the fetch idempotent (safe to re-run, safe against `since`
boundary edge cases where GitHub's inclusive filter could return the same
item twice) and needs no special-casing in tests.

### config.py

New optional key `github_repos` (list of `"owner/repo"` strings, default
`[]` — empty means the stage is skipped entirely, no credentials needed) and
`fetch_limit_per_repo` (default 50, same role as `fetch_limit_per_subreddit`).

### credentials.py

New required key `github_token` (personal access token), added to
`REQUIRED_KEYS` alongside the existing four — same all-or-nothing
`set_credentials.py` prompt flow already in place.

### db.py

One new function: `get_evidence_by_source_url(conn, source_url) -> dict |
None`. No schema changes — the `evidence` table already has every column
this source needs.

### run_weekly.py

`STAGES = ["fetch", "github_issues", "extract", "match", "report",
"materiality"]`. `run_all`/`main` call
`fetch_github.run(config["github_repos"], config["fetch_limit_per_repo"],
today=today)` between `fetch` and `extract` — a no-op when `github_repos` is
empty.

### extract.py

The extraction prompt currently opens with "You are analyzing a Reddit post
about a software product." Reword to source-neutral language ("You are
analyzing user feedback about a software product") since it will now also
classify GitHub issue/comment content. `SIGNAL_TYPES` is unchanged.

## Data model

No new tables. New evidence rows look like:

```json
{
  "evidence_id": "EV-2026-000210",
  "source_type": "github_issue",
  "source_name": "your-org/your-product",
  "source_url": "https://github.com/your-org/your-product/issues/482",
  "captured_at": "2026-09-06T00:00:00+00:00",
  "published_at": "2026-09-04T18:11:00+00:00",
  "title": "Feature request: dark mode",
  "content": "Would love a dark theme option...",
  "metadata": {"issue_number": 482, "state": "open", "labels": ["enhancement"], "comments_count": 3}
}
```

```json
{
  "evidence_id": "EV-2026-000211",
  "source_type": "github_issue_comment",
  "source_name": "your-org/your-product",
  "source_url": "https://github.com/your-org/your-product/issues/482#issuecomment-1234567",
  "captured_at": "2026-09-06T00:00:00+00:00",
  "published_at": "2026-09-05T09:30:00+00:00",
  "title": "Feature request: dark mode",
  "content": "+1, my eyes hurt at night",
  "metadata": {"issue_number": 482, "comment_id": 1234567}
}
```

## Error handling

Same pattern as every other stage: one DB transaction per
`fetch_github.run()` invocation, `conn.rollback()` and re-raise on any
unhandled exception, state saved only after the transaction commits (so a
failed run retries from the same cursor next time — never advances past data
that didn't persist). PyGithub exceptions (`RateLimitExceededException`,
`BadCredentialsException`, generic `GithubException`) are wrapped in a
module-local `FetchError`, following the same wrapping pattern
`fetch.py`/`_wrap_prawcore_error` already uses for PRAW.

## Testing

Fake PyGithub objects (`FakeGithubClient`/`FakeRepo`/`FakeIssue`/
`FakeComment`) mirroring the existing `FakeRedditClient`/`FakeSubreddit`/
`FakeSubmission` pattern already used for `fetch.py`'s tests. Unit tests for
`fetch_github.py` cover: first-run bootstrap (no cursor), cursor advancement
across two runs, an issue that gains a new comment on a later run (issue body
not re-inserted, new comment is), the dedup guard against a boundary replay,
PR filtering, and PyGithub error wrapping. `db.get_evidence_by_source_url`
gets a direct unit test in `test_db.py`. `test_end_to_end.py` gets a new
scenario mixing one Reddit post and one GitHub issue into the same canonical
topic, proving the merged evidence stream still reaches materiality/event
emission correctly.

## Open items for implementation planning

- Exact PyGithub fake-object shape needs to match whatever subset of
  attributes `fetch_github.py` actually reads (mirrors how
  `FakeSubmission` only implements the fields `fetch.py` uses) — to be
  pinned down task-by-task rather than guessed here.
- `fetch_limit_per_repo` caps issues per run but not comments per issue;
  acceptable for now since comment volume per issue is naturally bounded,
  but worth a comment in the code if it ever needs a cap.
- The all-or-nothing `REQUIRED_KEYS` credential prompt means a user who only
  wants Reddit is still prompted for `github_token`. Pre-existing pattern
  (already true for `anthropic_api_key` today); not addressed by this
  sub-project.
