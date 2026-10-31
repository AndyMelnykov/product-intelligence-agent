"""Detect duplicated or coordinated posting within one topic-week, from text and timestamps only.

No authors are stored (README privacy rule), so accounts can't be told apart. This looks for:
- near-duplicates: posts whose normalized text is at least NEAR_DUPLICATE_THRESHOLD similar. Each
  cluster counts once toward materiality ("effective mentions").
- bursts: at least BURST_MIN_POSTS posts within BURST_WINDOW_HOURS. A burst is added to the reasons,
  never subtracted: outages produce real bursts of distinct posts.
"""
import difflib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

NEAR_DUPLICATE_THRESHOLD = 0.85
BURST_WINDOW_HOURS = 2
BURST_MIN_POSTS = 4

_URL_RE = re.compile(r"https?://\S+")
_NON_WORD_RE = re.compile(r"[^\w]+")


def normalize(title, content):
    text = _URL_RE.sub(" ", f"{title or ''} {content or ''}".lower())
    return _NON_WORD_RE.sub(" ", text).strip()


def similarity(a, b):
    if not a or not b:
        return 1.0 if a == b else 0.0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    upper_bound = matcher.quick_ratio()
    return matcher.ratio() if upper_bound >= NEAR_DUPLICATE_THRESHOLD else upper_bound


def near_duplicate_clusters(evidence_rows, threshold=NEAR_DUPLICATE_THRESHOLD):
    texts = [(e["evidence_id"], normalize(e.get("title"), e.get("content"))) for e in evidence_rows]
    parent = {evidence_id: evidence_id for evidence_id, _ in texts}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if similarity(texts[i][1], texts[j][1]) >= threshold:
                parent[root(texts[j][0])] = root(texts[i][0])

    clusters = {}
    for evidence_id, _ in texts:
        clusters.setdefault(root(evidence_id), []).append(evidence_id)
    return sorted(clusters.values(), key=lambda c: c[0])


def bursts(evidence_rows, window_hours=BURST_WINDOW_HOURS, min_posts=BURST_MIN_POSTS):
    stamped = sorted((datetime.fromisoformat(e["published_at"]), e["evidence_id"]) for e in evidence_rows)
    window = timedelta(hours=window_hours)
    found, i = [], 0
    while i < len(stamped):
        j = i
        while j + 1 < len(stamped) and stamped[j + 1][0] - stamped[i][0] <= window:
            j += 1
        if j - i + 1 >= min_posts:
            found.append([evidence_id for _, evidence_id in stamped[i:j + 1]])
            i = j + 1
        else:
            i += 1
    return found


@dataclass
class Assessment:
    raw_count: int
    distinct_count: int
    duplicate_clusters: list
    bursts: list

    @property
    def collapsed(self):
        return self.raw_count - self.distinct_count

    def reasons(self):
        out = []
        if self.collapsed:
            out.append(f"{self.collapsed} near-duplicate post(s) counted once ({len(self.duplicate_clusters)} cluster(s))")
        out.extend(f"burst: {len(group)} posts within {BURST_WINDOW_HOURS}h" for group in self.bursts)
        return out


def assess(evidence_rows):
    clusters = near_duplicate_clusters(evidence_rows)
    return Assessment(len(evidence_rows), len(clusters), [c for c in clusters if len(c) > 1], bursts(evidence_rows))
