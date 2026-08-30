import json
from datetime import date

import materiality


def test_confidence_label_buckets_by_threshold():
    assert materiality.confidence_label(0.9) == "high"
    assert materiality.confidence_label(0.7) == "medium"
    assert materiality.confidence_label(0.3) == "low"


def test_classify_materiality_low_for_stable_trend():
    result = materiality.classify_materiality(
        signal_type="new_feature_demand", trend="stable", mentions_this_week=1, avg_confidence=0.9,
    )
    assert result["label"] == "low"


def test_classify_materiality_medium_for_rising_with_moderate_volume():
    result = materiality.classify_materiality(
        signal_type="usability_issue", trend="rising", mentions_this_week=3, avg_confidence=0.7,
    )
    assert result["label"] == "medium"


def test_classify_materiality_high_for_rising_with_high_volume_and_confidence():
    result = materiality.classify_materiality(
        signal_type="complaint_rising", trend="rising", mentions_this_week=6, avg_confidence=0.9,
    )
    assert result["label"] == "high"
    assert result["score"] == 0.8


def test_classify_materiality_high_for_event_driven_signal_type():
    result = materiality.classify_materiality(
        signal_type="pricing_change", trend="new", mentions_this_week=1, avg_confidence=0.95,
    )
    assert result["label"] == "high"


def test_classify_materiality_critical_for_critical_signal_type():
    result = materiality.classify_materiality(
        signal_type="implementation_deadline", trend="new", mentions_this_week=1, avg_confidence=0.9,
    )
    assert result["label"] == "critical"
    assert result["score"] == 0.95


def test_classify_materiality_low_for_event_driven_type_below_confidence_threshold():
    result = materiality.classify_materiality(
        signal_type="pricing_change", trend="new", mentions_this_week=1, avg_confidence=0.5,
    )
    assert result["label"] == "low"


def test_dominant_signal_type_returns_single_type():
    candidates = [
        {"evidence_id": "EV-2026-000001", "signal_type": "new_feature_demand", "summary": "s", "confidence": 0.9},
    ]
    assert materiality._dominant_signal_type(candidates) == "new_feature_demand"


def test_dominant_signal_type_returns_mixed_marker_for_multiple_types():
    candidates = [
        {"evidence_id": "EV-2026-000001", "signal_type": "new_feature_demand", "summary": "s", "confidence": 0.9},
        {"evidence_id": "EV-2026-000002", "signal_type": "usability_issue", "summary": "s2", "confidence": 0.8},
    ]
    assert materiality._dominant_signal_type(candidates) == "mixed_signal_types"


def test_build_material_signal_shape_for_rising_trend():
    topic = {"topic_id": "TOPIC-0001", "name": "Dark mode support"}
    candidates_this_week = [
        {"evidence_id": "EV-2026-000001", "signal_type": "new_feature_demand", "summary": "s", "confidence": 0.9},
        {"evidence_id": "EV-2026-000002", "signal_type": "new_feature_demand", "summary": "s2", "confidence": 0.8},
    ]
    materiality_result = {"label": "high", "score": 0.8, "reasons": ["trend is rising with 6 mentions this week"]}

    signal = materiality.build_material_signal(
        topic, candidates_this_week, trend="rising", materiality=materiality_result, today=date(2026, 8, 23),
    )

    assert signal["created_at"] == "2026-08-23T00:00:00+00:00"
    assert signal["signal_type"] == "new_feature_demand"
    assert signal["topic_id"] == "TOPIC-0001"
    assert signal["entity"] == {"type": "customer_topic", "topic_id": "TOPIC-0001", "topic_name": "Dark mode support"}
    assert signal["confidence_label"] == "high"
    assert signal["materiality_label"] == "high"
    assert signal["materiality_score"] == 0.8
    assert signal["evidence_ids"] == ["EV-2026-000001", "EV-2026-000002"]
    assert signal["change_type"] == "trend_change"
    assert signal["recommended_next_step"] == "strategic_assessment"


def test_build_material_signal_marks_new_event_and_urgent_step_for_critical():
    topic = {"topic_id": "TOPIC-0001", "name": "Dark mode support"}
    candidates_this_week = [
        {"evidence_id": "EV-2026-000001", "signal_type": "pricing_change", "summary": "s", "confidence": 0.95},
    ]
    materiality_result = {"label": "critical", "score": 0.95, "reasons": ["critical-impact signal type"]}

    signal = materiality.build_material_signal(
        topic, candidates_this_week, trend="new", materiality=materiality_result, today=date(2026, 8, 23),
    )

    assert signal["change_type"] == "new_event"
    assert signal["recommended_next_step"] == "urgent_strategic_assessment"


def test_emit_event_appends_jsonl_envelope(tmp_path):
    events_path = tmp_path / "events.jsonl"
    material_signal = {
        "signal_id": "SIG-2026-0001", "created_at": "2026-08-23T00:00:00+00:00",
        "signal_type": "new_feature_demand", "summary": "Dark mode support: rising",
        "confidence_label": "high", "materiality_label": "high",
        "evidence_ids": ["EV-2026-000001", "EV-2026-000002"],
    }

    envelope = materiality.emit_event(material_signal, events_path=str(events_path))

    assert envelope == {
        "event": "product_intelligence.signal.material",
        "event_version": "1.0",
        "timestamp": "2026-08-23T00:00:00+00:00",
        "signal": {
            "signal_id": "SIG-2026-0001", "signal_type": "new_feature_demand",
            "summary": "Dark mode support: rising", "confidence": "high", "materiality": "high",
            "evidence_ids": ["EV-2026-000001", "EV-2026-000002"],
        },
    }

    with open(events_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert json.loads(lines[0]) == envelope


def test_emit_event_appends_without_truncating_existing_events(tmp_path):
    events_path = tmp_path / "events.jsonl"
    signal_one = {
        "signal_id": "SIG-2026-0001", "created_at": "2026-08-23T00:00:00+00:00",
        "signal_type": "new_feature_demand", "summary": "first", "confidence_label": "high",
        "materiality_label": "high", "evidence_ids": ["EV-2026-000001"],
    }
    signal_two = {
        "signal_id": "SIG-2026-0002", "created_at": "2026-08-30T00:00:00+00:00",
        "signal_type": "new_feature_demand", "summary": "second", "confidence_label": "high",
        "materiality_label": "high", "evidence_ids": ["EV-2026-000002"],
    }

    materiality.emit_event(signal_one, events_path=str(events_path))
    materiality.emit_event(signal_two, events_path=str(events_path))

    with open(events_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["signal"]["signal_id"] == "SIG-2026-0002"
