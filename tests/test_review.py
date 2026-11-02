import json
from datetime import date
from types import SimpleNamespace

import pytest

import db
import registry
import review
from llm import LLMReply

WEEK = "2026-W33"
TODAY = date(2026, 8, 15)


class Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class Response:
    def __init__(self, text, stop_reason="end_turn", model="claude-opus-5-5"):
        self.content, self.stop_reason, self.model = [Text(text)], stop_reason, model


class BetaMessages:
    def __init__(self, responses):
        self.responses, self.kwargs = list(responses), []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, Response) else Response(result)


def client(*responses):
    return SimpleNamespace(beta=SimpleNamespace(messages=BetaMessages(responses)))


def seed(path):
    conn = db.connect(path)
    db.init_db(conn)
    old = db.insert_canonical_topic(conn, slug="dark-mode", name="Dark mode", description="wants dark theme",
                                    aliases=[], first_seen="2026-W30", last_seen="2026-W30")
    new = db.insert_canonical_topic(conn, slug="night-theme", name="Night theme", description="wants night theme",
                                    aliases=[], first_seen=WEEK, last_seen=WEEK)
    quiet = db.insert_canonical_topic(conn, slug="exports", name="Exports", description="export problems",
                                      aliases=[], first_seen="2026-W30", last_seen="2026-W30")
    evidence_id = db.insert_evidence(conn, source_type="reddit_post", source_name="s", source_url="/1",
                                     captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-15T00:00:00+00:00",
                                     title="t", content="c", metadata={})
    db.insert_signal_candidate(conn, evidence_id=evidence_id, signal_type="new_feature_demand",
                               summary="wants a night theme", confidence=0.9, topic_id=new)
    conn.commit()
    conn.close()
    return old, new, quiet


def merge_item(a, b, confidence=0.9):
    return {"kind": "merge", "from_topic_id": a, "into_topic_id": b, "reason": "same request", "confidence": confidence}


def reply(text, stop_reason="end_turn"):
    return LLMReply(text=text, stop_reason=stop_reason, input_tokens=None, output_tokens=None)


def test_focus_is_new_or_touched_topics_with_this_weeks_summaries(tmp_path):
    path = str(tmp_path / "pi.db")
    old, new, quiet = seed(path)
    conn = db.connect(path)

    focus, registry = review.build_review_input(conn, WEEK)

    assert [t["topic_id"] for t in focus] == [new]
    assert focus[0]["this_week_summaries"] == ["wants a night theme"]
    assert focus[0]["new_this_week"] is True
    assert [t["topic_id"] for t in registry] == [old, new, quiet]


def test_parse_keeps_valid_items_and_drops_the_rest():
    valid, focus = {"T1", "T2", "T3"}, {"T2"}
    raw = json.dumps([
        merge_item("T2", "T1"),
        merge_item("T2", "T2"),                                       # into itself
        merge_item("T3", "T1"),                                       # no focus topic involved
        merge_item("T2", "T9"),                                       # unknown topic
        {"kind": "split", "topic_id": "T2", "reason": "two issues", "confidence": 0.7},
        {"kind": "split", "topic_id": "T3", "reason": "x", "confidence": 0.7},   # not in focus
        {"kind": "merge", "from_topic_id": "T2", "into_topic_id": "T1", "reason": "", "confidence": 0.9},
        merge_item("T2", "T1", confidence=1.5),
        "junk",
    ])

    items, dropped = review.parse_review_reply(reply(raw), valid, focus)

    assert [(i["kind"], i["from_topic_id"], i["topic_id"]) for i in items] == [("merge", "T2", None), ("split", None, "T2")]
    assert dropped == 7


@pytest.mark.parametrize("text,stop_reason", [("{}", "end_turn"), ("not json", "end_turn"), ("[]", "refusal"),
                                               ("[]", "max_tokens")])
def test_parse_rejects_unusable_replies(text, stop_reason):
    with pytest.raises(review.ReviewResponseError):
        review.parse_review_reply(reply(text, stop_reason), {"T1"}, {"T1"})


def test_run_calls_opus_with_fallbacks_and_stores_items(tmp_path):
    path = str(tmp_path / "pi.db")
    old, new, _ = seed(path)
    fake = client(json.dumps([merge_item(new, old)]))

    assert review.run(db_path=path, today=TODAY, client=fake) == 1

    sent = fake.beta.messages.kwargs[0]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["betas"] == ["server-side-fallback-2026-07-01"] and sent["fallbacks"] == "default"
    assert sent["output_config"] == {"effort": "high"}
    assert "thinking" not in sent
    conn = db.connect(path)
    [item] = db.get_review_items(conn, status="open")
    assert (item["kind"], item["from_topic_id"], item["into_topic_id"], item["week"]) == ("merge", new, old, WEEK)
    call = conn.execute("SELECT stage, subject_key, status FROM llm_call").fetchone()
    assert tuple(call) == ("review", WEEK, "succeeded")


def test_run_once_per_week_unless_forced_and_never_duplicates_or_resurrects(tmp_path):
    path = str(tmp_path / "pi.db")
    old, new, _ = seed(path)
    review.run(db_path=path, today=TODAY, client=client(json.dumps([merge_item(new, old)])))

    assert review.run(db_path=path, today=TODAY, client=client()) == 0       # no call made: client has no answers

    conn = db.connect(path)
    [item] = db.get_review_items(conn)
    db.resolve_review_item(conn, item["item_id"], "rejected", resolved_by="human:test", note="different issues")
    conn.commit()
    conn.close()

    assert review.run(db_path=path, today=TODAY, client=client(json.dumps([merge_item(old, new)])), force=True) == 0
    conn = db.connect(path)
    assert len(db.get_review_items(conn)) == 1


def test_run_records_refusals_and_raises(tmp_path):
    path = str(tmp_path / "pi.db")
    seed(path)

    with pytest.raises(review.ReviewResponseError):
        review.run(db_path=path, today=TODAY, client=client(Response("", stop_reason="refusal")))

    conn = db.connect(path)
    assert conn.execute("SELECT status FROM llm_call").fetchone()[0] == "response_error"


def test_run_with_nothing_new_makes_no_call(tmp_path):
    path = str(tmp_path / "pi.db")
    seed(path)
    assert review.run(db_path=path, today=date(2026, 9, 30), client=client()) == 0


def stored(path, items):
    review.run(db_path=path, today=TODAY, client=client(json.dumps(items)))
    conn = db.connect(path)
    return conn, db.get_review_items(conn, status="open")


def test_accept_merges_the_topics_and_closes_the_item(tmp_path):
    path = str(tmp_path / "pi.db")
    old, new, _ = seed(path)
    conn, [item] = stored(path, [merge_item(new, old)])

    result = review.accept_item(conn, item["item_id"], week=WEEK, by="human:test")
    conn.commit()

    assert result["into"] == old
    assert db.get_canonical_topic(conn, new)["status"] == "merged"
    closed = db.get_review_item(conn, item["item_id"])
    assert (closed["status"], closed["resolved_by"]) == ("accepted", "human:test")
    lineage = db.get_lineage_for_topic(conn, new)[0]
    assert f"review item {item['item_id']}" in lineage["decided_by"]


def test_accept_refuses_splits_and_already_resolved_items(tmp_path):
    path = str(tmp_path / "pi.db")
    old, new, _ = seed(path)
    conn, items = stored(path, [{"kind": "split", "topic_id": new, "reason": "two issues", "confidence": 0.7},
                                merge_item(new, old)])
    split_item, merge = items

    with pytest.raises(review.ReviewError, match="registry.py split"):
        review.accept_item(conn, split_item["item_id"], week=WEEK, by="h")
    review.accept_item(conn, merge["item_id"], week=WEEK, by="h")
    with pytest.raises(review.ReviewError, match="not open"):
        review.accept_item(conn, merge["item_id"], week=WEEK, by="h")


def test_accept_fails_cleanly_when_the_registry_changed(tmp_path):
    path = str(tmp_path / "pi.db")
    old, new, quiet = seed(path)
    conn, [item] = stored(path, [merge_item(new, old)])
    registry.merge_topics(conn, "night-theme", "exports", week=WEEK, reason="by hand", decided_by="h")
    conn.commit()

    with pytest.raises(registry.RegistryError):
        review.accept_item(conn, item["item_id"], week=WEEK, by="h")
    conn.rollback()

    assert db.get_review_item(conn, item["item_id"])["status"] == "open"


def test_cli_list_reject_and_done(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    old, new, _ = seed(path)
    stored(path, [merge_item(new, old), {"kind": "split", "topic_id": new, "reason": "two", "confidence": 0.6}])
    capsys.readouterr()

    assert review.main(["--db-path", path, "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [i["kind"] for i in listed] == ["merge", "split"]

    assert review.main(["--db-path", path, "reject", str(listed[0]["item_id"]), "--reason", "different"]) == 0
    assert review.main(["--db-path", path, "done", str(listed[1]["item_id"]), "--note", "split by hand"]) == 0
    conn = db.connect(path)
    assert [i["status"] for i in db.get_review_items(conn)] == ["rejected", "accepted"]


def test_cli_accept_reports_registry_errors_with_exit_code_2(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    assert review.main(["--db-path", path, "accept", "999"]) == 2
    assert "no review item" in capsys.readouterr().err


def test_health_counts_open_review_items(tmp_path):
    path = str(tmp_path / "pi.db")
    old, new, _ = seed(path)
    conn, _ = stored(path, [merge_item(new, old)])
    assert db.pipeline_health(conn, max_match_attempts=3)["review_items_open"] == 1
