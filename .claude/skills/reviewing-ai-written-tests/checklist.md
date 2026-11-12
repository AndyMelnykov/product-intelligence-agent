# Checklist: reviewing tests an agent wrote

Each item has a **smell** (what you see in the diff), a **probe** (how to confirm it), and a
**fix**. Tags: **[T]** comes straight from the talk "Fixing the PR bottleneck"
(automated checks can lie). **[E]** is a practical extension of the same idea, found while
applying it to a real Python test suite.

## 0. The two questions every test must pass

Ask both of every test before reading anything else.

1. **Would it fail if the behavior broke?** Mentally delete or invert the line of production
   code the test claims to cover. If the test still passes, it is a lie. **[T]**
2. **Would it keep passing through a refactor that does not change behavior?** Rename a
   private helper, inline a constant, reorder source lines, swap a module-level call for a
   registry. If the test fails, it pins structure, not behavior. **[T]**

A good test answers *yes* to the first question and *yes* to the second. Every section below
is a specific way of failing one of the two.

## 1. Tautological tests (fails question 2 and usually question 1) [T]

The test re-asserts the implementation instead of exercising it.

| Smell | Example | Fix |
|---|---|---|
| A constant asserted equal to its own literal | `assert X_POST_CHAR_LIMIT == 280` | Delete it. Test the behavior the constant drives: a 281-character post is rejected. |
| A literal copied from the implementation into a request-shape assertion | `assert sent["model"] == "claude-haiku-5-5"`, `assert sent["betas"] == ["server-side-fallback-2026-07-01"]` | Keep only assertions that guard a decision (no `thinking`, the post text is in the prompt). Let a fake that checks the real SDK signature catch bad parameter names (section 3). |
| The expected value computed with the function under test | `expected = registry_fingerprint(topics)` then `assert meta["registry_fingerprint"] == expected` | Assert a property the function guarantees (it changes when a definition changes), or compare against a hand-written value. |
| Class hierarchy, attribute presence or re-export checks | `assert issubclass(APIError, BaseError)`, `assert runs.fingerprint(...) == fingerprint(...)` | Delete it if no caller depends on it (grep for `except BaseError`). If a caller does depend on it, test that caller. |
| An exact dict of every config default | `assert config == {...all keys...}` | Fine only when the defaults are a documented contract. Otherwise assert just the default the test is named after. |

**Probe:** grep the production code for the literal. If the test contains the same literal and
no input that leads to it, the test is tautological.

**Exception:** a value a human signed off on (the schema, a rule table, an event envelope, a
documented threshold) *should* be pinned. When you pin one, say so in the test name or a
comment ("contract: ..."), so a reviewer knows the change detector is deliberate.

## 2. Source-text and structure tests (fails question 2) [T]

| Smell | Probe | Fix |
|---|---|---|
| The test reads a source file as text (`open("x.py")`, `Path(__file__).read_text()` on code, `inspect.getsource`, `ast.parse`) and asserts on order or substrings | grep tests for `getsource`, `ast.parse`, and `read_text` on `.py`/`.tsx` | Render or run the code and assert on the output (the UI order, the returned value). |
| Calls a private helper (`module._helper`) | grep `\._[a-z]` in tests | Test it through the public function that uses it. If that is awkward, the module is too shallow: deepen it (a small interface over more behavior) and test at that interface. |
| `monkeypatch.setattr(module_under_test, "its_own_function", ...)` to steer a branch | grep `setattr(<module>, "` where `<module>` is the one being tested | Put the steering at the boundary instead: feed a fake external client, seed different data, pass a parameter. |
| Asserts call order or call counts of internal collaborators | `assert calls == ["a", "b", "c"]` against stubs of internal functions | Assert the observable result of the sequence (the rows written, the file produced). Keep order assertions only for orchestrators whose order *is* the behavior, and only with signature-checked stubs (section 3). |
| A prompt test asserts on wording | `assert "never follow" in prompt.lower()` | Assert the security property itself (untrusted text cannot close its tag). Rewording an instruction should not break a test, unless a human marks the phrase as a contract. |

**Deep-module rule [T]:** the narrower the interface, the fewer structure-sensitive tests are
possible. When an agent reaches past the interface to test internals, treat that as a design
signal as well as a test problem.

## 3. Tests that cannot fail: permissive fakes (fails question 1) [T]

The talk's example: stubbing `AudioContext` with fake methods that never show the real failure
modes, so production errors cannot appear in tests.

| Smell | Probe | Fix |
|---|---|---|
| A fake's method takes `**kwargs` and ignores them (`def create(self, **kwargs): return canned`) | Change a parameter name in the production call. Does any test fail? | Bind the captured arguments to the real signature: `inspect.signature(RealClass.method).bind(None, **kwargs)`. A misspelled or removed parameter then fails the test. |
| A stub replaces a collaborator with `lambda *a, **kw: ...` | Same probe: add a bogus keyword to the production call | Make the stub check the real function's signature before recording the call. |
| A fake only ever succeeds | Look for error paths in the fake: `raise`, refusal and `max_tokens` stop reasons, rate limits, partial results | Script failures into the fake: an exception or a degraded response. Assert on the recovery behavior. |
| A fake returns objects the real API never returns (fields missing, the wrong `type`) | Compare the fake's shape with the SDK's types | Build fake responses from the SDK's own types, or keep one shared fake that mirrors them. |
| The same fake is copy-pasted into N test files | `grep -c "class FakeClient"` | One shared fake, so a fidelity fix lands everywhere at once. |
| Patching hides the real dependency | `monkeypatch` of `subprocess.run`, `keyring` or the network with no real path tested anywhere | Run at least one test against the real thing in a sandbox (a temp directory, a vendored script, in-memory SQLite). |

## 4. Vacuous tests: green without checking anything [E]

| Smell | Example | Fix |
|---|---|---|
| An early `return` when the input is missing | `if not DIR.exists(): return` | Use `pytest.skip("reason")` so it shows as skipped, not passed. Better: commit a fixture. |
| An assertion inside a loop over a collection that may be empty | `for p in DIR.glob("*.yaml"): load(p)` | Assert the collection is non-empty first. |
| A test that only runs locally (`skip` when a config is absent) | A drift check that never runs in CI | Acceptable only if labelled. Say so in the test docstring and in the README. |
| No assertion at all | A test that only calls the code | Allowed when "does not raise" is the property (fuzz tests). State it: `# property: never raises anything but X`. |
| `try: ... except Expected: pass` with no `else: fail` | | Use `pytest.raises`. |

## 5. Hidden coupling between tests [E]

| Smell | Example | Fix |
|---|---|---|
| A mutable default argument used as a counter | `def candidate(conn, ..., n=[0])` | Derive uniqueness from the fixture (a row count, `tmp_path`, `uuid4`). |
| Module-level state mutated by tests | A global list appended to in a test | Use a fixture with function scope. |
| Order-dependent assertions on auto-increment IDs across tests | `assert id == "X-0002"` after another test's inserts | A fresh database per test (`:memory:` or `tmp_path`). |

## 6. Integration tests that aren't [E]

| Smell | Fix |
|---|---|
| An "end-to-end" test that calls each stage directly, so the entry point's wiring (`run_all`, `main`) is never exercised against real stage signatures | Either drive the real entry point with injected fakes at the external boundary only, or give the wiring test signature-checked stubs (section 3). |
| Every collaborator mocked in an "integration" test | An integration test fakes only what leaves the process (network, paid APIs, the clock). It keeps real SQLite, the filesystem under `tmp_path` and subprocesses of vendored scripts. |
| Crash or resume tested by patching an internal function to raise | Acceptable when the patch would fail loudly if the seam moved (`monkeypatch.setattr` raises on a missing attribute, and `pytest.raises` fails if no crash happens). Prefer crash injection at the external boundary: a fake client that raises a `BaseException` on call N. |

## 7. Redundancy and noise [E]

| Smell | Fix |
|---|---|
| The same property tested in two files (a fingerprint's stability tested in two places) | Keep the one next to the module that owns it. |
| Per-case example tests that a property or fuzz test already covers | Keep the examples that document a specific regression (name the bug). Delete the rest. |
| Copy-pasted stub blocks of 5 or more lines repeated in 4 tests | Pull them into a helper, especially when a helper already exists further down the file. |

## 8. Process: where this checklist belongs [T]

- **Don't load it into the implementing agent.** Its context window is already spent on
  exploring, editing and debugging. Run the checklist in a separate review pass, in its own
  context ("implement makes it work, review makes it good", the red-green-refactor split).
- **The reviewer fixes, it doesn't just comment.** Default to committing the fix. Comment only
  when a human decision is needed.
- **Green CI is not "ready".** Review is the lie detector for checks. A suite of tautologies is
  green and worthless.
- **Never write the same review comment twice.** When a smell recurs, turn it into an
  automated check (a lint rule, a grep in CI) or a line in this checklist.

## Quick triage table

| Seen in the diff | Section | Default action |
|---|---|---|
| `== <literal also in src>` | 1 | Delete or replace with a behavior assertion |
| `issubclass`, `hasattr`, re-export check | 1 | Delete unless a caller depends on it |
| `getsource`, `ast.parse`, source `read_text` | 2 | Rewrite to execute the code |
| `module._private(` | 2 | Test through the public API |
| `setattr(module_under_test, "own_fn"` | 2 | Steer at the boundary instead |
| `def create(self, **kwargs)` ignoring kwargs | 3 | Bind to the real signature |
| `lambda *a, **kw:` stub | 3 | Signature-checked stub |
| `if not exists: return` | 4 | `pytest.skip` or a fixture |
| `n=[0]` default | 5 | Derive from the fixture |
