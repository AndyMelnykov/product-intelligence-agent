from pathlib import Path

from evals import harness

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "evals" / "golden"


def test_committed_extraction_golden_set_is_valid():
    examples = harness.load_golden_dir(GOLDEN_DIR / "extraction", "extraction")

    assert any(e.expected["skip"] for e in examples)
    assert any(not e.expected["skip"] for e in examples)


def test_committed_matching_golden_set_is_valid():
    scenarios = harness.load_golden_dir(GOLDEN_DIR / "matching", "matching")

    assert {s.name for s in scenarios} >= {"connection_pooling_duplicate", "mixed_new_and_existing"}
