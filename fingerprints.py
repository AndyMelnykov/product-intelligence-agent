"""Short, stable content fingerprints for prompts, golden data, and registry snapshots."""
import hashlib
import json


def fingerprint(value):
    """First 12 hex chars of a SHA-256 over a string, or over any JSON-serializable value."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
