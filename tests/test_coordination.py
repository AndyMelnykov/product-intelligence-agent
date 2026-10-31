import coordination


def row(evidence_id, title, content, published_at="2026-08-14T10:00:00+00:00"):
    return {"evidence_id": evidence_id, "title": title, "content": content, "published_at": published_at}


def test_normalize_drops_case_urls_and_punctuation():
    assert coordination.normalize("Dark MODE!!", "see https://x.y/z now") == "dark mode see now"


def test_near_duplicates_cluster_and_distinct_posts_stay_apart():
    rows = [
        row("E1", "Switching to AcmeNotes", "Your app is too slow, switching to AcmeNotes today."),
        row("E2", "switching to acmenotes!", "Your app is too slow; switching to AcmeNotes today"),
        row("E3", "Export bug", "CSV export drops the last column when names contain commas."),
    ]
    assert coordination.near_duplicate_clusters(rows) == [["E1", "E2"], ["E3"]]


def test_burst_needs_enough_posts_inside_the_window():
    stamps = ["2026-08-14T10:00:00+00:00", "2026-08-14T10:30:00+00:00", "2026-08-14T11:10:00+00:00",
              "2026-08-14T11:50:00+00:00", "2026-08-14T18:00:00+00:00"]
    rows = [row(f"E{i}", f"t{i}", f"distinct body number {i} " * (i + 1), s) for i, s in enumerate(stamps)]
    assert coordination.bursts(rows) == [["E0", "E1", "E2", "E3"]]
    assert coordination.bursts(rows[:3]) == []


def test_assess_counts_clusters_once_and_explains_itself():
    rows = [row(f"E{i}", "Switching to AcmeNotes", "Your app is too slow, switching to AcmeNotes today.",
                f"2026-08-14T10:{i:02d}:00+00:00") for i in range(5)]
    rows.append(row("E9", "Unrelated", "The calendar widget shows the wrong week number in January.",
                    "2026-08-14T20:00:00+00:00"))

    a = coordination.assess(rows)

    assert (a.raw_count, a.distinct_count, a.collapsed) == (6, 2, 4)
    assert a.reasons() == ["4 near-duplicate post(s) counted once (1 cluster(s))", "burst: 5 posts within 2h"]
