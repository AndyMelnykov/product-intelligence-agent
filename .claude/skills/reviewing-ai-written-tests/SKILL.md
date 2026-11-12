---
name: reviewing-ai-written-tests
description: Use when reviewing a diff or test suite that an agent wrote or touched, when tests pass but you doubt they would catch a regression, or when a test asserts constants, reads source text, calls private helpers, monkeypatches the module under test, or uses fakes that accept any arguments
---

# Reviewing AI-written tests

## Overview

Automated checks can lie. Agents write tests in good faith that pass forever: they re-assert
the implementation, read source text, or stub dependencies so loosely that nothing can fail.
Review is the lie detector.

**Core principle:** a test earns its place only if it would **fail when the behavior breaks**
and **survive a refactor that keeps the behavior**.

## When to use

- Reviewing a PR or session diff that adds or changes tests
- Auditing an existing suite for tests that cannot fail
- After an agent says "all tests pass" on code you have not watched fail

Do not use this skill while implementing. Run it as a separate review pass, so the
implementer's context stays free for the work.

## How to review

1. Apply the two questions to each test. Mentally break the covered line: does the test fail?
   Mentally refactor without changing behavior: does it still pass?
2. Grep for the smells in the quick triage table in [checklist.md](checklist.md).
3. Confirm each suspect with its probe. Measure, don't guess: for a permissive fake, bind the
   captured kwargs to the real signature and see whether anything breaks.
4. Classify each finding as **delete**, **rewrite at the interface**, **tighten the fake**, or
   **needs a human decision** (the value is a human-owned contract: schema, rule table,
   prompt wording).
5. As the reviewer, make the fixes yourself. Leave a comment only where a human must decide.
6. A smell seen twice becomes an automated check or a new checklist line.

## Quick reference

| Seen in the diff | Default action |
|---|---|
| A literal that also appears in the source | Delete it, or assert the behavior it drives |
| `issubclass`, re-export or attribute-presence check | Delete unless a caller depends on it |
| Source read as text (`getsource`, `ast.parse`) | Execute the code instead |
| `module._private(...)` | Test through the public API |
| `monkeypatch.setattr(module_under_test, "own_fn", ...)` | Steer at the external boundary |
| A fake `create(**kwargs)` that ignores its kwargs, or a `lambda *a, **kw` stub | Bind the call to the real signature |
| `if not path.exists(): return` | Use `pytest.skip(reason)` |
| A mutable default used as a counter in a helper | Derive uniqueness from the fixture |

The full checklist, with probes, fixes and exceptions: [checklist.md](checklist.md).

## Common mistakes

- **Deleting pinned contracts.** A test that pins a human-approved value (the schema, a
  materiality threshold, an event envelope) is a deliberate change detector. Keep it and label
  it.
- **Calling every monkeypatch bad.** Crash injection at a seam is fine if the patch fails
  loudly when the seam moves. The smell is patching to *steer* the logic under test.
- **Trusting green.** A passing suite of tautologies is green and worthless. Run the probes.
