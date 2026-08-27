# feedback-reddit-parser

Automatically surface what people are saying about your product on Reddit — without
having to read every thread yourself.

## The problem

Customers and potential customers talk about your product on Reddit, but that feedback
is scattered across subreddits, buried in comment threads, and easy to miss. Manually
reading every thread doesn't scale, and a lot of what's there is noise: AI-generated
replies, off-topic chatter, low-effort comments that don't say anything useful.

## What this does

A weekly pipeline that:

1. **Collects** — pulls new posts from a configured list of subreddits via the official
   Reddit API.
2. **Extracts signal** — uses Claude to pull out the actual topic from each post
   (feature request, complaint, praise, question, competitor comparison), filtering out
   AI slop and irrelevant noise.
3. **Amplifies** — matches new topics against a running registry of canonical topics, so
   a subject that keeps coming back across threads, gets more comments, or attracts more
   upvotes shows up as a stronger, growing signal rather than a one-off mention.
4. **Reports** — turns the registry into a ranked view of what's trending, rising, or
   fading, as structured JSON/CSV you can feed into your own tools.

The result is an ongoing, low-effort read on what your product's community actually cares
about, instead of anecdotal impressions from whichever thread you happened to see.

## Status

Implemented — see
[docs/superpowers/specs/2026-08-15-reddit-signal-pipeline-design.md](docs/superpowers/specs/2026-08-15-reddit-signal-pipeline-design.md)
for the original pipeline architecture and
[docs/superpowers/specs/2026-08-25-evidence-data-model-migration-design.md](docs/superpowers/specs/2026-08-25-evidence-data-model-migration-design.md)
for the evidence/signal-candidate/canonical-topic data model it now runs on, plus the
matching implementation plans in
[docs/superpowers/plans/](docs/superpowers/plans/).

## Architecture

Four independent pipeline stages chained by a single entrypoint, backed by a SQLite
database (`data/pi_agent.db`) instead of flat JSON files. Each stage is a plain Python
module and can also be run standalone.

```text
run_weekly.py
  └─> fetch.py    → evidence table
  └─> extract.py  → signal_candidate table
  └─> match.py    → canonical_topic + topic_weekly_mentions tables
  └─> report.py   → data/reports/<week>.json, data/reports/<week>.csv
```

- **fetch.py** — pulls new posts per subreddit via PRAW (official Reddit API wrapper),
  storing each as an immutable `evidence` row.
- **extract.py** — classifies each not-yet-processed evidence row into a `signal_type`
  (from the product intelligence signal taxonomy) plus a confidence score, storing it as
  a `signal_candidate`.
- **match.py** — matches new candidates against the `canonical_topic` registry (or
  creates a new topic), incrementing per-week mention counts.
- **report.py** — computes top topics by mention count and week-over-week trend
  (new / rising / stable / falling) from the database, same output format as before.

Use `python query.py topic <slug>` or `python query.py search <keyword>` for ad hoc
inspection of the database during development.

Credentials (Reddit API + Anthropic API keys) are stored in the OS credential vault via
`keyring` — never in a plaintext file, never committed to the repo.

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

## Reddit API compliance

Built on the official Reddit API (via PRAW), not HTML scraping — only configured public
subreddits, minimal data retention (no author/username stored), and rate limiting
handled by PRAW's built-in throttling. See the design spec for details.

## License

See [LICENSE](LICENSE).
