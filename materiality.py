import json
import os
from datetime import date, datetime, timezone

import db
import report
from weekutil import iso_week_string

EVENT_DRIVEN_HIGH_SIGNAL_TYPES = {
    "product_launch", "feature_launch", "major_feature_improvement", "feature_removal",
    "product_end_of_life", "product_end_of_support", "pricing_change", "packaging_change",
    "free_tier_change", "acquisition", "partnership", "major_customer_win", "api_change",
    "licensing_change", "distribution_change", "new_model_capability", "new_ai_api_capability",
    "open_source_release", "framework_deprecation", "major_platform_feature",
    "ecosystem_standard_change", "platform_policy_change", "marketplace_policy_change",
    "vendor_integration_change", "infrastructure_pricing_change", "cloud_platform_capability_launch",
}

EVENT_DRIVEN_CRITICAL_SIGNAL_TYPES = {
    "regulation_announced", "regulation_approved", "implementation_deadline",
    "compliance_requirement_change",
}

MATERIALITY_LABEL_SCORE = {"low": 0.2, "medium": 0.5, "high": 0.8, "critical": 0.95}

CONFIDENCE_HIGH_THRESHOLD = 0.85
CONFIDENCE_MEDIUM_THRESHOLD = 0.6
EVENT_DRIVEN_CONFIDENCE_THRESHOLD = 0.85
HIGH_TREND_MENTIONS_THRESHOLD = 5
HIGH_TREND_CONFIDENCE_THRESHOLD = 0.85
MEDIUM_TREND_MENTIONS_THRESHOLD = 3


def confidence_label(avg_confidence):
    if avg_confidence >= CONFIDENCE_HIGH_THRESHOLD:
        return "high"
    if avg_confidence >= CONFIDENCE_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def classify_materiality(signal_type, trend, mentions_this_week, avg_confidence):
    if signal_type in EVENT_DRIVEN_CRITICAL_SIGNAL_TYPES and avg_confidence >= EVENT_DRIVEN_CONFIDENCE_THRESHOLD:
        return {
            "label": "critical", "score": MATERIALITY_LABEL_SCORE["critical"],
            "reasons": [f"{signal_type} is a critical-impact signal type",
                        f"confirmed with confidence {avg_confidence:.2f}"],
        }
    if signal_type in EVENT_DRIVEN_HIGH_SIGNAL_TYPES and avg_confidence >= EVENT_DRIVEN_CONFIDENCE_THRESHOLD:
        return {
            "label": "high", "score": MATERIALITY_LABEL_SCORE["high"],
            "reasons": [f"{signal_type} is a high-impact signal type",
                        f"confirmed with confidence {avg_confidence:.2f}"],
        }
    if (trend == "rising" and mentions_this_week >= HIGH_TREND_MENTIONS_THRESHOLD
            and avg_confidence >= HIGH_TREND_CONFIDENCE_THRESHOLD):
        return {
            "label": "high", "score": MATERIALITY_LABEL_SCORE["high"],
            "reasons": [f"trend is rising with {mentions_this_week} mentions this week",
                        f"average confidence {avg_confidence:.2f} is high"],
        }
    if trend in ("rising", "new") and mentions_this_week >= MEDIUM_TREND_MENTIONS_THRESHOLD:
        return {
            "label": "medium", "score": MATERIALITY_LABEL_SCORE["medium"],
            "reasons": [f"trend is {trend} with {mentions_this_week} mentions this week"],
        }
    return {
        "label": "low", "score": MATERIALITY_LABEL_SCORE["low"],
        "reasons": [f"trend is {trend} with {mentions_this_week} mentions this week"],
    }


def _dominant_signal_type(candidates):
    types = {c["signal_type"] for c in candidates}
    return next(iter(types)) if len(types) == 1 else "mixed_signal_types"


def build_material_signal(topic, candidates_this_week, trend, materiality, today):
    evidence_ids = sorted({c["evidence_id"] for c in candidates_this_week})
    avg_confidence = sum(c["confidence"] for c in candidates_this_week) / len(candidates_this_week)
    signal_type = _dominant_signal_type(candidates_this_week)
    created_at = datetime(today.year, today.month, today.day, tzinfo=timezone.utc).isoformat()

    return {
        "created_at": created_at,
        "signal_type": signal_type,
        "topic_id": topic["topic_id"],
        "entity": {"type": "customer_topic", "topic_id": topic["topic_id"], "topic_name": topic["name"]},
        "summary": f"{topic['name']}: {trend} ({materiality['reasons'][0]})",
        "confidence_label": confidence_label(avg_confidence),
        "materiality_label": materiality["label"],
        "materiality_score": materiality["score"],
        "materiality_reasons": materiality["reasons"],
        "evidence_ids": evidence_ids,
        "change_type": "new_event" if trend == "new" else "trend_change",
        "recommended_next_step": (
            "urgent_strategic_assessment" if materiality["label"] == "critical" else "strategic_assessment"
        ),
    }


EVENT_NAME = "product_intelligence.signal.material"
EVENT_VERSION = "1.0"
DEFAULT_EVENTS_PATH = "data/events.jsonl"


def emit_event(material_signal, events_path=DEFAULT_EVENTS_PATH):
    envelope = {
        "event": EVENT_NAME,
        "event_version": EVENT_VERSION,
        "timestamp": material_signal["created_at"],
        "signal": {
            "signal_id": material_signal["signal_id"],
            "signal_type": material_signal["signal_type"],
            "summary": material_signal["summary"],
            "confidence": material_signal["confidence_label"],
            "materiality": material_signal["materiality_label"],
            "evidence_ids": material_signal["evidence_ids"],
        },
    }
    os.makedirs(os.path.dirname(events_path) or ".", exist_ok=True)
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(envelope) + "\n")
    return envelope


def _week_of_iso_timestamp(iso_timestamp):
    return iso_week_string(datetime.fromisoformat(iso_timestamp).date())


def run(db_path="data/pi_agent.db", trend_window_weeks=8, today=None, events_path=DEFAULT_EVENTS_PATH):
    run_date = today or date.today()
    week = iso_week_string(run_date)
    conn = db.connect(db_path)
    db.init_db(conn)

    topics = db.get_canonical_topics(conn)
    for topic in topics:
        topic["weekly_mentions"] = db.get_topic_weekly_mentions(conn, topic["topic_id"])
    trend_by_topic_id = {row["id"]: row for row in report.compute_trends(topics, week, trend_window_weeks)}

    created_signals = []
    try:
        for topic in topics:
            candidates = db.get_candidates_for_topic(conn, topic["topic_id"])
            candidates_this_week = [c for c in candidates if _week_of_iso_timestamp(c["captured_at"]) == week]
            if not candidates_this_week:
                continue

            trend_row = trend_by_topic_id[topic["topic_id"]]
            avg_confidence = sum(c["confidence"] for c in candidates_this_week) / len(candidates_this_week)
            signal_type = _dominant_signal_type(candidates_this_week)
            materiality_result = classify_materiality(
                signal_type=signal_type, trend=trend_row["trend"],
                mentions_this_week=trend_row["mentions_this_week"], avg_confidence=avg_confidence,
            )
            if materiality_result["label"] not in ("high", "critical"):
                continue

            material_signal = build_material_signal(
                topic, candidates_this_week, trend_row["trend"], materiality_result, run_date,
            )
            signal_id = db.insert_material_signal(conn, **material_signal)
            created_signals.append({**material_signal, "signal_id": signal_id})
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.commit()
    conn.close()

    for material_signal in created_signals:
        emit_event(material_signal, events_path=events_path)


if __name__ == "__main__":
    run()
