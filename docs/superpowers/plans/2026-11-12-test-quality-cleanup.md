# Test Quality Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove or rewrite the tests that would not catch a regression, or that break on a behavior-preserving refactor, as found by auditing `tests/` against the review checklist.

**Architecture:** A test-only change. Fakes of external boundaries (the Anthropic SDK, the pipeline stages called by `run_weekly`) start checking calls against the real signatures. Structure-only tests are deleted. Tests of private helpers move to the public interface. Tests that patch the module under test steer through data or the external boundary instead.

**Tech Stack:** Python 3.11, pytest, hypothesis, `inspect.signature().bind`, anthropic SDK (installed unpinned in CI via `requirements.txt`).

**Spec:** `.claude/skills/reviewing-ai-written-tests/checklist.md` (sections cited per task as §N).

## Audit baseline (2026-11-12)

- `pytest`: **470 passed, 1 skipped**.
- Probe 1 bound the kwargs each stage sends (extract, triage, match, judge, paraphrase, review via beta) to the installed SDK's `Messages.create` signature: **all bind**. No live bug. The risk is latent: the fakes would not notice if a parameter were removed or misspelled.
- Probe 2 bound every call `run_weekly.run_all` and `main --stage X` make to the real stage signatures: **no mismatches**. Same conclusion.

## Global Constraints

- Test code only. If a step seems to need a production-code change, stop and ask.
- Do not touch `db.SCHEMA_SQL`, `db.MIGRATIONS`, any `*_PROMPT_TEMPLATE`, `SIGNAL_TYPES`, or the `materiality.py` rule table (CLAUDE.md "Who designs what").
- No real API calls in pytest. Pass `encoding="utf-8"` to every file read and write. No 3.12+ syntax.
- `ruff check .` and `pytest` green after every task.
- Each task ends with a mutation check: break the production line the task's tests claim to cover, watch them fail, then revert. Paste the failing line into the PR.

## Review Focus

1. **SDK upgrade moves `anthropic.resources.beta.Messages`.** The shared fake must fail with an import error naming the path, not silently skip checking. Covered in Task 1, Step 1, by importing at module top.
2. **A call the real SDK accepts gets rejected** (a parameter passed via `extra_body`, a positional argument). The fake binds `(None, **kwargs)` only. The existing call sites are keyword-only (probe 1), so no test should break. Any break in Task 1 Step 4 is a real finding: report it, don't loosen the fake.
3. **A migrated test silently loses an assertion.** Task 1 and Task 2 compare `pytest --collect-only -q | tail -1` before and after: the counts must match.
4. **The paraphrase test starts skipping and hides a broken loader** once paraphrase files are committed. Task 6's version asserts at least one file loads when the directory exists.
5. **The escalation test passes for the wrong reason** (a second signal from a different topic). Task 5's assertion selects by `topic_id`.

---

### Task 1: SDK-checked shared Anthropic fake (§3)

Today each test file has its own fake (`FakeClient`, `FakeMessages`, `Client`, `Messages`, `BetaMessages`, `ByModel`, `CrashOnCall`, `RecordingMessages`, `CapturingMessages`, `Repeating`, `RepeatingMessages`) that accepts any kwargs.

**Files:**
- Create: `tests/anthropic_fake.py`
- Create: `tests/test_anthropic_fake.py`
- Modify (replace local fakes or add the check): `tests/test_extract.py`, `tests/test_match.py`, `tests/test_triage.py`, `tests/test_review.py`, `tests/test_end_to_end.py`, `tests/test_resume.py`, `tests/test_evals_judge.py`, `tests/test_evals_harness.py`, `tests/test_evals_runners.py`, `tests/test_evals_triage.py`, `tests/test_evals_oracles.py`

**Interfaces:**
- Produces: `check_sdk_call(kwargs: dict, *, beta: bool = False) -> None`, which raises `TypeError` when `kwargs` don't bind to `anthropic.resources.Messages.create` (or to `anthropic.resources.beta.Messages.create` when `beta=True`).
- Produces: `text_response(text: str, *, stop_reason: str = "end_turn", thinking_first: bool = False, model: str = "fake-model")`, which returns an object with `.content` (a list of blocks with `.type` and `.text`), `.stop_reason` and `.model`.
- Produces: `FakeAnthropic(responses: list, *, beta: bool = False)` with `.messages.create(**kwargs)` (or `.beta.messages.create` when `beta=True`). Each call runs `check_sdk_call`, appends `kwargs` to `.calls`, then pops the next response: a `str` becomes `text_response(str)`, an `Exception` or `BaseException` instance is raised, any other object is returned as is.

- [ ] **Step 1: Write the failing tests** in `tests/test_anthropic_fake.py`
  - `test_fake_rejects_a_parameter_the_sdk_does_not_accept`: `pytest.raises(TypeError)` on `FakeAnthropic(["x"]).messages.create(model="m", max_tokens=1, messages=[], tempurature=0)`.
  - `test_fake_records_calls_and_returns_scripted_text`: `create(model="m", max_tokens=1, messages=[])` returns an object whose `.content[0].text == "x"`, and `.calls == [{"model": "m", "max_tokens": 1, "messages": []}]`.
  - `test_beta_fake_accepts_betas_and_fallbacks`: `FakeAnthropic(["x"], beta=True).beta.messages.create(model="m", max_tokens=1, messages=[], betas=["b"], fallbacks="default")` does not raise.
- [ ] **Step 2:** `pytest tests/test_anthropic_fake.py -v`. Expected: FAIL (`ModuleNotFoundError: anthropic_fake`).
- [ ] **Step 3:** Implement `tests/anthropic_fake.py` with the interfaces above. Import both `Messages` classes at module top (Review Focus 1).
- [ ] **Step 4:** Record `pytest --collect-only -q | tail -1`. Migrate every listed file: delete identical local fakes and import from `anthropic_fake`. Special-purpose fakes (`ByModel`, `CrashOnCall`, `RecordingMessages`, `CapturingMessages`, `Repeating*`, review's `BetaMessages`) keep their routing logic but call `check_sdk_call(kwargs)` (or `beta=True`) first.
- [ ] **Step 5:** `pytest`. Expected: same collected count, all pass. A `TypeError` from `check_sdk_call` is a real finding (Review Focus 2).
- [ ] **Step 6: Mutation check.** Rename `max_tokens` to `max_token` in `extract.request_extraction`. `pytest tests/test_extract.py` must fail with `TypeError`. Revert.
- [ ] **Step 7:** Commit `test: shared Anthropic fake that checks calls against the SDK signature`.

### Task 2: Signature-checked stage stubs in `test_run_weekly.py` (§3, §6)

Every stub is `lambda *a, **kw: ...`, and no other test runs `run_all` or `main` against the real stages, so a renamed stage parameter would pass the whole suite.

**Files:**
- Modify: `tests/test_run_weekly.py` (the stub blocks at lines 15-19, 37-41, 54-58, 71-75 and 87-89, and the `_stub_stages` helper at 112-114)

**Interfaces:**
- Produces: `_stub_stages(monkeypatch, calls: list, *, record_kwargs: dict | None = None) -> None`. It replaces `run` on fetch, extract, match, report, materiality, review and publish. Each stub runs `inspect.signature(real_run).bind(*a, **kw)`, appends the stage name to `calls`, and when `record_kwargs` is given stores `record_kwargs[stage] = (a, kw)`.

- [ ] **Step 1:** Record `pytest tests/test_run_weekly.py --collect-only -q | tail -1`.
- [ ] **Step 2:** Rewrite `_stub_stages` as above, then use it in every test. `test_run_all_calls_every_stage_in_order` keeps its assertions on fetch args `(["sub1"], 25)` and `trend_window_weeks == 4` for report and materiality, read from `record_kwargs`. `test_run_all_passes_the_cascade_flag_to_extract` reads `record_kwargs["extract"][1]["cascade"] is True`. The two publish tests keep their custom `publish.run` replacement, wrapped with the same bind check.
- [ ] **Step 3:** `pytest tests/test_run_weekly.py -v`. Expected: same count, all pass.
- [ ] **Step 4: Mutation check.** In `run_weekly.run_all`, change `extract.run(today=today, cascade=...)` to `extract.run(today=today, cascad=...)`. `pytest tests/test_run_weekly.py` must fail with `TypeError`. Today it passes. Revert.
- [ ] **Step 5:** Commit `test(run_weekly): stage stubs check the real stage signatures`.

### Task 3: Delete structure-only and duplicate tests (§1, §7)

Grep confirmed that no production code has `except ExtractionError` or `except MatchError`, and that `runs.fingerprint` is already exercised by every eval-runner test.

**Files:**
- Modify: `tests/test_extract.py`: delete `test_extraction_error_subclasses_share_base_class` (lines 140-142).
- Modify: `tests/test_match.py`: delete `test_match_error_subclasses_share_base_class` (lines 172-174).
- Modify: `tests/test_db_migrations.py`: delete `test_fingerprint_is_stable_for_strings_and_json_values` (19-22, a duplicate of `tests/test_evals_runs.py::test_fingerprint_is_short_stable_and_content_sensitive`) and `test_runs_module_still_exposes_fingerprint` (25-27). Drop the now-unused `fingerprint` import.

- [ ] **Step 1:** Delete the four tests. `ruff check .`, then `pytest`. Expected: 4 fewer passed, nothing else changes.
- [ ] **Step 2: Mutation check** (proves the remaining coverage). Remove `fingerprint` from `evals/runs.py`'s imports. `pytest tests/test_evals_runners.py` must fail. Revert.
- [ ] **Step 3:** Commit `test: drop class-hierarchy, re-export and duplicate fingerprint tests`.

### Task 4: Test private helpers through the public interface (§2)

**Files:**
- Modify: `tests/test_materiality.py`: replace `test_dominant_signal_type_returns_single_type` and `test_dominant_signal_type_returns_mixed_marker_for_multiple_types` (lines 70-82). The single-type case is already covered by `test_build_material_signal_shape_for_rising_trend`.
- Modify: `tests/test_db_migrations.py`: replace `test_add_column_is_idempotent` (89-94).

- [ ] **Step 1:** Write `test_build_material_signal_marks_mixed_signal_types`. Call `materiality.build_material_signal(topic, candidates, trend="rising", materiality={"label": "high", "score": 0.8, "reasons": []}, today=date(2026, 8, 23))` with two candidates of types `new_feature_demand` and `usability_issue`. Assert `signal["signal_type"] == "mixed_signal_types"`.
- [ ] **Step 2:** Write `test_init_db_upgrades_a_legacy_database_that_already_has_a_migrated_column`. Use `legacy_connection(path)`, run `ALTER TABLE evidence ADD COLUMN source_key TEXT`, commit, close, then `db.connect` and `db.init_db`. Assert `db.schema_version(conn) == max(v for v, _ in db.MIGRATIONS)` and that `"source_key"` is in `[r[1] for r in conn.execute("PRAGMA table_info(evidence)")]`.
- [ ] **Step 3:** Delete the three replaced tests. Run `pytest tests/test_materiality.py tests/test_db_migrations.py`. Expected: pass.
- [ ] **Step 4: Mutation check.** Make `db._add_column` unconditional (drop its `if`). The Step 2 test must fail with `duplicate column name`. Revert.
- [ ] **Step 5:** Commit `test: cover dominant signal type and column migration through public APIs`.

### Task 5: Steer through data or the boundary, not by patching the module under test (§2)

**Files:**
- Modify: `tests/test_fetch.py::test_run_keeps_earlier_subreddits_when_a_later_one_fails_unexpectedly` (lines 247-259)
- Modify: `tests/test_materiality.py::test_escalation_within_a_week_emits_a_second_signal` (lines 310-326)

- [ ] **Step 1:** In the fetch test, remove the `monkeypatch.setattr(fetch, "fetch_new_posts", ...)`. Build the client as `FakeRedditClient({"suba": FakeSubreddit([make_submission("t3_a1")]), "subb": FakeErrorSubreddit(RuntimeError("bug"))})`. `fetch_new_posts` wraps only `PrawcoreException`, so the `RuntimeError` propagates. The assertions are unchanged.
- [ ] **Step 2:** In the escalation test, remove the `classify_materiality` patch. Between the two runs, `UPDATE signal_candidate SET signal_type = 'implementation_deadline' WHERE topic_id = ?` for the seeded topic, then commit. Confidence is 0.9 against the 0.85 critical threshold, so the real rule table yields `critical`. Select the signals `WHERE topic_id = ?` (Review Focus 5). The rest of the assertions are unchanged.
- [ ] **Step 3:** `pytest tests/test_fetch.py tests/test_materiality.py`. Expected: pass.
- [ ] **Step 4: Mutation check.** In `materiality.run`, change `if previous and LABEL_RANK[previous["materiality_label"]] >= LABEL_RANK[...]` (around line 207) to `if previous:`, so any same-week rerun is suppressed. The escalation test must fail. Revert. Then confirm the old patched version of the test would also have caught this. It would have, so the rewrite's gain is that it now goes through the real rule table rather than a forced label.
- [ ] **Step 5:** Commit `test: steer fetch and escalation tests through data, not module patches`.

### Task 6: Vacuous and coupled tests (§4, §5)

**Files:**
- Modify: `tests/test_evals_golden.py::test_committed_paraphrase_files_are_valid` (27-32). `evals/golden/paraphrases/` does not exist, so the test passes having checked nothing.
- Modify: `tests/test_registry.py::candidate` (line 23, the `n=[0]` mutable default shared across tests).
- Modify: `tests/test_export_matching_scenario.py` (51-55: hand-written `try/raise AssertionError/except`), plus the unused `capsys` parameters at 42 and 64.

- [ ] **Step 1:** Paraphrase test: `pytest.skip("no committed paraphrase files")` when the directory is missing or `glob("*.yaml")` is empty. Otherwise load each file and assert the result is non-empty.
- [ ] **Step 2:** `candidate()`: remove `n`. Use `source_url=f"/{conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0] + 1}"`.
- [ ] **Step 3:** Use `with pytest.raises(harness.GoldenSchemaError, match="draft scenario"):`, and drop the unused `capsys`.
- [ ] **Step 4:** `pytest`. Expected: passed count down by 1, skipped count up by 1 (the paraphrase test now reports as skipped).
- [ ] **Step 5:** Commit `test: make the paraphrase check visible when skipped; remove shared counter state`.

### Task 7 (needs a human decision first): literal echoes of model IDs and beta names (§1)

These assertions copy strings from the implementation. After Task 1 the fake checks parameter *names*. These literals check only that the source still contains the same string:

- `tests/test_triage.py:53`: `sent["model"] == "claude-haiku-5-5"`
- `tests/test_review.py:119-120`: `sent["model"] == "claude-opus-5-5"`, `sent["betas"] == ["server-side-fallback-2026-07-01"]`
- `tests/test_evals_triage.py:87`: `record["models"] == {"triage": "claude-haiku-5-5"}`

Proposal: keep the assertions that guard a decision (`"thinking" not in sent`, `output_config == {"effort": ...}`, the post text is in the prompt). Change the model assertions to the decision they stand for (`"haiku" in sent["model"]`, `"opus" in sent["model"]`). Delete the beta-string equality. **Ask before doing this.** If model choice is a reviewed contract, mark these as deliberate pins with a `# contract:` comment instead.

---

## Considered and kept (with reason)

| Test | Why it stays |
|---|---|
| `test_materiality.py` score pins (`0.8`, `0.95`) | The rule table is human-designed (ADR 003). A deliberate change detector. |
| `test_config.py::test_load_config_applies_defaults_when_omitted` (exact dict) | The defaults are user-facing, documented config. |
| `test_triage.py:96`, `test_extract.py:574` (`"never follow" in prompt`) | Prompt wording is human-owned (CLAUDE.md). Rewording is a prompt change that should trip a test. Flag to the owner if that is unwanted. |
| Report and comparison format tests (`format_report`, `format_comparison`) | The text output *is* the behavior. |
| Crash injection via `match.apply_resolutions` and `materiality.flush_outbox` patches | Fails loudly if the seam moves (`setattr` raises on a missing attribute, and `pytest.raises` fails if no crash happens). |
| `extract.in_audit_sample` patches | It is the sampling seam. The real sampler has its own test (`test_audit_sample_is_deterministic_and_close_to_the_rate`). |
| `test_publish.py::test_vendored_signals_py_matches_repo_path_when_configured` | A local-only drift check that skips visibly in CI. Already labelled by its skip messages. |
| `test_match.py::test_match_call_records_the_registry_fingerprint` (expected value via `registry_fingerprint`) | Tests the wiring (the fingerprint is taken at call time). The function's own properties are tested in `test_registry.py`. |
| Extraction error-path tests via `extract_topic` that overlap the parser and fuzz tests | Each names a past regression (no evidence id, non-object JSON). Cheap. |
