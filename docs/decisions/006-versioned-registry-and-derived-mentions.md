# Decision

Keep the registry's history and derive mention counts from evidence:

- every topic definition change is a `topic_version` row;
- merges and splits are explicit human operations recorded in `topic_lineage`;
- each candidate keeps its `original_topic_id`;
- `topic_weekly_mentions` is a cache rebuilt per topic from non-duplicate candidates, keyed by the ISO week of `captured_at`.

## Context

The README names split and merged topics as the main failure mode of matching, but there was no way to fix one: no merge, no split, no history. Weekly counts were incremented by the week the match stage ran, so a late run put mentions in the wrong week, and counts could drift from the candidates they summarize.

## Options considered

- **Mutable registry plus a manual SQL fix.** Rejected: loses history, error-prone, not reproducible.
- **Full event sourcing of the registry.** Rejected: rebuilding the registry on every read is more machinery than one SQLite file needs.
- **Latest view plus append-only history tables** (chosen): `canonical_topic` stays the "latest" view, so reports, materiality and query don't change. History lives beside it.

## Consequences

- `python registry.py merge|split|history|snapshot|rebuild-mentions`. Merges and splits are human decisions. P5's review queue can propose them but never applies them on its own.
- Reports keep using the latest view. `registry.py snapshot --week W` and the scenario export use the as-of view.
- Counts can always be recomputed (`rebuild-mentions`). Rebuilding re-keys counts that older code keyed by run week.
- The matcher sees aliases (former names of merged topics) and follows merged ids, so stored or stale decisions land on the surviving topic.
