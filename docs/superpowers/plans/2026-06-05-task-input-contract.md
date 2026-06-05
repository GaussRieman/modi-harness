# run_task Input Contract Formalization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the implicit `run_task` input contract to a documented single source of truth — a `TaskInput` type plus a shared `task_input_to_text()` helper — and fix the two places where the contract's absence caused wrong behavior (`prompt` key unrecognized; subagent dispatcher passing `str(dict)`).

**Architecture:** One new pure helper module in the leaf `_utils` package holds the precedence logic. Both the top-level adapter and the subagent dispatcher call it, eliminating duplicate/divergent logic. A `total=False` `TaskInput` TypedDict documents recognized keys without narrowing the accepted `dict[str, Any]` type, so the change is non-breaking. Docs in three files are updated to point at the now-explicit contract.

**Tech Stack:** Python 3.12, TypedDict, pytest, langchain-core test doubles.

**Reference spec:** `docs/superpowers/specs/2026-06-05-task-input-contract-design.md`

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/modi_harness/_utils/task_input.py` | Create | Pure `task_input_to_text()` precedence logic (single source of truth) |
| `src/modi_harness/_utils/__init__.py` | Modify | Export `task_input_to_text` |
| `src/modi_harness/types.py` | Modify | Add `TaskInput` TypedDict |
| `src/modi_harness/graph/harness_adapter.py` | Modify | Call shared helper; delete private `_input_to_user_text` |
| `src/modi_harness/subagent/dispatcher.py` | Modify | Call shared helper instead of `str(child_input)` |
| `tests/_utils/test_task_input.py` | Create | Unit tests for precedence, every key, fallback |
| `tests/subagent/test_e2e.py` | Modify | Add test locking the dispatcher bug fix |
| `tests/test_types.py` | Modify | Add `TaskInput` shape test |
| `docs/architecture/08-harness-api.md` | Modify | Add "TaskInput payload" subsection (precedence table) |
| `docs/types-reference.md` | Modify | Add `TaskInput` to §15 Harness API Types |
| `docs/cli.md` | Modify | Point to recognized-key set near the `prompt` example |

---

## Task 1: Shared `task_input_to_text` helper (with `prompt` key)

This is the heart of the change: the precedence logic as a pure, tested, public function. It reproduces the current `_input_to_user_text` behavior and **adds `prompt`** to the recognized keys.

**Files:**
- Create: `src/modi_harness/_utils/task_input.py`
- Modify: `src/modi_harness/_utils/__init__.py`
- Test: `tests/_utils/test_task_input.py`

- [ ] **Step 1: Write the failing test**

Create `tests/_utils/test_task_input.py`:

```python
"""Tests for task_input_to_text — the run_task input → user-text contract."""

from __future__ import annotations

from modi_harness._utils import task_input_to_text


def test_messages_last_user_wins() -> None:
    payload = {
        "messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
    }
    assert task_input_to_text(payload) == "second"


def test_prompt_key_recognized() -> None:
    assert task_input_to_text({"prompt": "hello"}) == "hello"


def test_customer_message_key() -> None:
    assert task_input_to_text({"customer_message": "charged twice"}) == "charged twice"


def test_question_key() -> None:
    assert task_input_to_text({"question": "why?"}) == "why?"


def test_goal_key() -> None:
    assert task_input_to_text({"goal": "resolve it"}) == "resolve it"


def test_precedence_full_chain() -> None:
    # messages (user) beats everything
    payload = {
        "messages": [{"role": "user", "content": "M"}],
        "prompt": "P",
        "customer_message": "C",
        "question": "Q",
        "goal": "G",
    }
    assert task_input_to_text(payload) == "M"
    # remove messages -> prompt
    del payload["messages"]
    assert task_input_to_text(payload) == "P"
    # remove prompt -> customer_message
    del payload["prompt"]
    assert task_input_to_text(payload) == "C"
    # remove customer_message -> question
    del payload["customer_message"]
    assert task_input_to_text(payload) == "Q"
    # remove question -> goal
    del payload["question"]
    assert task_input_to_text(payload) == "G"


def test_messages_without_user_falls_through() -> None:
    # messages present but no role=="user" → continue to next key (prompt)
    payload = {
        "messages": [{"role": "assistant", "content": "only assistant"}],
        "goal": "fallback goal",
    }
    assert task_input_to_text(payload) == "fallback goal"


def test_empty_payload_falls_back_to_str() -> None:
    assert task_input_to_text({}) == "{}"


def test_unrecognized_only_falls_back_to_str() -> None:
    payload = {"unknown_key": "x"}
    assert task_input_to_text(payload) == str(payload)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/_utils/test_task_input.py -v`
Expected: FAIL with `ImportError: cannot import name 'task_input_to_text'`

- [ ] **Step 3: Create the helper module**

Create `src/modi_harness/_utils/task_input.py`:

```python
"""Derive the agent's first user message from a run_task input payload.

This is the single source of truth for the ``ModiSession.run_task`` /
``stream`` / ``astream`` input contract. See
docs/architecture/08-harness-api.md for the documented precedence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Scalar keys checked in order after the ``messages`` list. The first key
# present in the payload wins. ``prompt`` was added when the contract was
# formalized; the rest predate it.
_TEXT_KEYS = ("prompt", "customer_message", "question", "goal")


def task_input_to_text(payload: Mapping[str, Any]) -> str:
    """Return the text used as the agent's first user message.

    Precedence: ``messages`` (content of the last ``role == "user"`` item)
    > ``prompt`` > ``customer_message`` > ``question`` > ``goal`` >
    ``str(payload)`` fallback. If ``messages`` is present but contains no
    user item, evaluation continues to the scalar keys.
    """
    messages = payload.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                return str(msg.get("content", ""))
    for key in _TEXT_KEYS:
        if key in payload:
            return str(payload[key])
    return str(payload)
```

- [ ] **Step 4: Export from `_utils/__init__.py`**

Modify `src/modi_harness/_utils/__init__.py`. Add the import (after the existing `from .ids import new_ulid` line) and add `"task_input_to_text"` to `__all__`:

```python
from .frontmatter import parse_frontmatter
from .hashing import canonical_json, compute_context_hash, compute_fingerprint
from .ids import new_ulid
from .task_input import task_input_to_text
from .time import now_iso

__all__ = [
    "canonical_json",
    "compute_context_hash",
    "compute_fingerprint",
    "new_ulid",
    "now_iso",
    "parse_frontmatter",
    "task_input_to_text",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/_utils/test_task_input.py -v`
Expected: PASS (9 passed)

- [ ] **Step 6: Commit**

```bash
git add src/modi_harness/_utils/task_input.py src/modi_harness/_utils/__init__.py tests/_utils/test_task_input.py
git commit -m "feat: add shared task_input_to_text helper with prompt key

Single source of truth for the run_task input contract. Reproduces the
existing _input_to_user_text precedence and adds 'prompt' as a recognized
key so {\"prompt\": ...} payloads resolve correctly."
```

---

## Task 2: Unify the top-level adapter on the shared helper

Replace the private `_input_to_user_text` with the shared helper and delete the duplicate.

**Files:**
- Modify: `src/modi_harness/graph/harness_adapter.py` (call site line ~347; definition lines ~425-433)

- [ ] **Step 1: Add the import**

In `src/modi_harness/graph/harness_adapter.py`, modify the existing `_utils` import (currently `from .._utils import new_ulid`) to:

```python
from .._utils import new_ulid, task_input_to_text
```

- [ ] **Step 2: Update the call site**

In `_seed_state`, change the message content line (was `content=_input_to_user_text(request.input)`):

```python
            "messages": [
                Message(  # type: ignore[typeddict-item]
                    role="user",
                    content=task_input_to_text(request.input),
                    tool_call_id=None,
                    metadata={},
                )
            ],
```

- [ ] **Step 3: Delete the private function**

Remove the entire `_input_to_user_text` definition at the bottom of the file (the `def _input_to_user_text(payload: dict[str, Any]) -> str:` block, ~lines 425-433, including its body through `return str(payload)`).

- [ ] **Step 4: Verify no remaining references**

Run: `grep -rn "_input_to_user_text" src tests`
Expected: no output (zero matches)

- [ ] **Step 5: Run adapter + smoke tests to verify behavior preserved**

Run: `uv run pytest tests/graph tests/test_smoke.py tests/test_smoke_scenarios.py -q`
Expected: PASS (all existing tests green — refactor is behavior-preserving for `messages`/`goal` inputs)

- [ ] **Step 6: Commit**

```bash
git add src/modi_harness/graph/harness_adapter.py
git commit -m "refactor: use shared task_input_to_text in harness adapter

Replaces the private _input_to_user_text with the shared _utils helper.
No behavior change for messages/goal inputs; prompt is now recognized."
```

---

## Task 3: Fix the subagent dispatcher bug

The dispatcher seeds the child's first message with `str(child_input)` — a stringified dict — instead of the derived text. Route it through the shared helper.

**Files:**
- Modify: `src/modi_harness/subagent/dispatcher.py` (import block ~line 23; child-state seed ~line 113)
- Test: `tests/subagent/test_e2e.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/subagent/test_e2e.py`. This uses the existing `_Script`/`_agent`/`_session` helpers already in the file. The child model captures the human-message text it actually receives, so we assert it is the derived text, not `str(dict)`:

```python
# ----------------------------------------------------------------------
# 10. Delegated child receives derived user text, not str(dict)
# ----------------------------------------------------------------------


def test_child_receives_derived_text_not_stringified_dict(tmp_path: Path) -> None:
    """Regression: dispatcher must seed the child's first user message via
    task_input_to_text, not str(child_input). Delegating task={"goal": "X"}
    must give the child a user message of "X", never "{'goal': 'X'}"."""

    class _Capturing(_Script):
        seen_user_text: dict[str, list[str]] = Field(default_factory=dict)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            agent_name = self._sniff(messages)
            # Record the last human-role message content this agent saw.
            for m in messages:
                if m.__class__.__name__ == "HumanMessage":
                    self.seen_user_text.setdefault(agent_name, []).append(
                        str(getattr(m, "content", ""))
                    )
            return super()._generate(messages, stop, run_manager, **kwargs)

    research = _agent(tmp_path, "research", tools=[])
    lead = _agent(
        tmp_path,
        "lead",
        tools=["delegate_to_research"],
        allowed_subagents=["research"],
        subagents=[research],
    )
    script = _Capturing(
        by_agent={
            "lead": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delegate_to_research",
                            "args": {
                                "task": {"goal": "summarize the report"},
                                "rationale": "need facts",
                            },
                            "id": "tc1",
                        }
                    ],
                ),
                AIMessage(content="Final reply."),
            ],
            "research": [AIMessage(content="Summary done.")],
        }
    )
    h = _session(tmp_path, script, [lead])
    response = h.run_task(
        agent="lead", input={"goal": "go"}, thread_id="t-derived"
    )
    assert response["status"] == "completed"

    child_texts = script.seen_user_text.get("research", [])
    assert child_texts, "child model never received a human message"
    joined = " ".join(child_texts)
    assert "summarize the report" in joined
    assert "{'goal'" not in joined  # the bug: stringified dict must not appear
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest "tests/subagent/test_e2e.py::test_child_receives_derived_text_not_stringified_dict" -v`
Expected: FAIL — `joined` contains `{'goal': 'summarize the report'}`, so the `"summarize the report" in joined` passes but `assert "{'goal'" not in joined` fails (the stringified dict is present).

- [ ] **Step 3: Add the import to the dispatcher**

In `src/modi_harness/subagent/dispatcher.py`, modify the existing `_utils` import (currently `from .._utils import compute_fingerprint, new_ulid, now_iso`) to include the helper, keeping alphabetical order:

```python
from .._utils import compute_fingerprint, new_ulid, now_iso, task_input_to_text
```

- [ ] **Step 4: Fix the child-state seed**

In `dispatch_subagent`, change the child message content (was `content=str(child_input)`):

```python
        "messages": [
            Message(  # type: ignore[typeddict-item]
                role="user",
                content=task_input_to_text(child_input),
                tool_call_id=None,
                metadata={},
            )
        ],
```

- [ ] **Step 5: Run the new test to verify it passes**

Run: `uv run pytest "tests/subagent/test_e2e.py::test_child_receives_derived_text_not_stringified_dict" -v`
Expected: PASS

- [ ] **Step 6: Run the full subagent suite to verify no regressions**

Run: `uv run pytest tests/subagent -q`
Expected: PASS (all existing subagent tests still green)

- [ ] **Step 7: Commit**

```bash
git add src/modi_harness/subagent/dispatcher.py tests/subagent/test_e2e.py
git commit -m "fix: delegated subagents receive derived user text, not str(dict)

The dispatcher seeded the child's first message with str(child_input),
so delegated subagents saw a stringified dict (e.g. \"{'goal': 'x'}\")
as their user message. Route through the shared task_input_to_text helper."
```

---

## Task 4: Add the `TaskInput` TypedDict

Document the recognized keys as a type. `total=False` keeps every existing `dict[str, Any]` call site valid.

**Files:**
- Modify: `src/modi_harness/types.py` (add after the `Message` TypedDict, ~line 153)
- Test: `tests/test_types.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_types.py`. First add `TaskInput` to the import block at the top (the `from modi_harness.types import (...)` list, in alphabetical position between `StreamEvent` and `ThreadInfo`), then add the test function:

```python
def test_task_input_recognized_keys() -> None:
    # total=False: every field optional; a plain dict is a valid TaskInput.
    ti: TaskInput = {
        "messages": [{"role": "user", "content": "hi"}],
        "prompt": "hi",
        "customer_message": "hi",
        "question": "hi",
        "goal": "hi",
        "tags": ["billing"],
        "reference_keys": ["refund_policy"],
    }
    assert ti["tags"] == ["billing"]
    # An empty payload is also a valid TaskInput.
    empty: TaskInput = {}
    assert empty == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_types.py::test_task_input_recognized_keys -v`
Expected: FAIL with `ImportError: cannot import name 'TaskInput'`

- [ ] **Step 3: Add the TypedDict**

In `src/modi_harness/types.py`, add immediately after the `Message` TypedDict (which ends at the `metadata: dict[str, Any]` line, ~line 153):

```python
class TaskInput(TypedDict, total=False):
    """Input payload for ModiSession.run_task / stream / astream.

    All keys are optional and the dict may carry additional keys the agent
    expects. The harness derives the agent's first user message from these
    keys in priority order: messages > prompt > customer_message > question
    > goal, falling back to str(payload). ``tags`` and ``reference_keys``
    additionally steer memory selection. See
    docs/architecture/08-harness-api.md for the authoritative precedence.
    """

    messages: list[Message]
    prompt: str
    customer_message: str
    question: str
    goal: str
    tags: list[str]
    reference_keys: list[str]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_types.py::test_task_input_recognized_keys -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modi_harness/types.py tests/test_types.py
git commit -m "feat: add TaskInput TypedDict documenting run_task input keys

total=False, so existing dict[str, Any] call sites remain valid. Documents
recognized keys (messages/prompt/customer_message/question/goal/tags/
reference_keys) without narrowing the accepted type."
```

---

## Task 5: Document the contract

Make `08-harness-api.md` the authoritative prose, add `TaskInput` to the type reference, and fix the `cli.md` example.

**Files:**
- Modify: `docs/architecture/08-harness-api.md`
- Modify: `docs/types-reference.md`
- Modify: `docs/cli.md`

- [ ] **Step 1: Add the TaskInput payload subsection to `08-harness-api.md`**

In `docs/architecture/08-harness-api.md`, immediately after the Execution code block (the one ending with the `astream(...)` signature line, ~line 136) and before the line `` `run_task` is a thin wrapper over the stream ``, insert:

```markdown
#### `input` payload (TaskInput)

`input` is an open dict; the harness derives the agent's first user message
from recognized keys, in precedence order (first match wins):

| Priority | Key | Rule |
|---|---|---|
| 1 | `messages` | `content` of the last item with `role == "user"` |
| 2 | `prompt` | used as the message text |
| 3 | `customer_message` | used as the message text |
| 4 | `question` | used as the message text |
| 5 | `goal` | used as the message text |
| 6 | *(fallback)* | `str(payload)` — the whole dict stringified |

If `messages` is present but has no `role == "user"` item, evaluation
continues to `prompt`. Two further keys steer memory selection without
affecting the first message: `tags` (filters project-scope memory) and
`reference_keys` (selects reference-scope memory by name). The recognized
shape is typed as `TaskInput` (see types-reference.md §15). Passing both
`messages` and `goal` — as the examples do — means `messages` wins and
`goal` is an unused label; either alone is sufficient.
```

- [ ] **Step 2: Add `TaskInput` to `types-reference.md` §15**

In `docs/types-reference.md`, in the §15 "Harness API Types" code block, add the `TaskInput` definition immediately before `class RunTaskRequest(TypedDict):`:

```python
class TaskInput(TypedDict, total=False):
    # All optional; extra keys allowed. First-match precedence for the
    # agent's first user message: messages > prompt > customer_message >
    # question > goal > str(payload). tags / reference_keys steer memory.
    # See docs/architecture/08-harness-api.md for authoritative precedence.
    messages: list[Message]
    prompt: str
    customer_message: str
    question: str
    goal: str
    tags: list[str]
    reference_keys: list[str]
```

- [ ] **Step 3: Fix the `cli.md` example**

In `docs/cli.md`, replace the sentence describing `task.json` (currently lines ~28-29, "Where `task.json` is the input payload accepted by `ModiSession.run_task` (any shape your agent expects — typically `{"prompt": "..."}`).") with:

```markdown
Where `task.json` is the input payload accepted by `ModiSession.run_task`. The
harness derives the agent's first user message from recognized keys —
`messages`, `prompt`, `customer_message`, `question`, or `goal` (see
[Harness API](architecture/08-harness-api.md) for precedence). A minimal
payload is `{"prompt": "..."}` or `{"messages": [{"role": "user", "content": "..."}]}`.
```

- [ ] **Step 4: Verify docs reference the contract consistently**

Run: `grep -rn "task_input_to_text\|TaskInput\|str(payload)" docs/architecture/08-harness-api.md docs/types-reference.md`
Expected: `TaskInput` appears in both files; the precedence/`str(payload)` rule appears in `08-harness-api.md`.

- [ ] **Step 5: Commit**

```bash
git add docs/architecture/08-harness-api.md docs/types-reference.md docs/cli.md
git commit -m "docs: document the run_task TaskInput contract

Adds the recognized-key precedence table to the harness API doc, the
TaskInput type to the type reference, and fixes the cli.md prompt example
(now correct since prompt is a recognized key)."
```

---

## Task 6: Final full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -q`
Expected: PASS (all tests green, including the new `tests/_utils/test_task_input.py`, the new dispatcher regression test, and the new `TaskInput` type test)

- [ ] **Step 2: Confirm no orphaned references remain**

Run: `grep -rn "_input_to_user_text" src tests docs`
Expected: no output (the private function is fully removed and unreferenced)

- [ ] **Step 3: Confirm the dispatcher no longer stringifies input**

Run: `grep -n "str(child_input)" src/modi_harness/subagent/dispatcher.py`
Expected: no output (the bug line is gone)

---

## Self-Review Notes

**Spec coverage check (against `2026-06-05-task-input-contract-design.md`):**
- §3.1 `TaskInput` type → Task 4 ✓
- §3.2 shared helper → Task 1 ✓
- §2 `prompt` key addition → Task 1 (helper) + Task 5 (cli.md fix) ✓
- §3.3 unify call sites (adapter + dispatcher, delete private fn) → Tasks 2 & 3 ✓
- §4 docs (3 files) → Task 5 ✓
- §5 tests (helper precedence, dispatcher fix, regression guard) → Tasks 1, 3, and the smoke runs in Task 2 ✓
- §6 risks (additive `prompt`, behavior-preserving refactor) → covered by the "behavior preserved" smoke runs in Task 2 Step 5 and full suite in Task 6 ✓

**Type/name consistency:** `task_input_to_text` and `TaskInput` are used identically across all tasks. `_TEXT_KEYS` precedence (`prompt`, `customer_message`, `question`, `goal`) matches the table in Task 5 and the spec §2. The dispatcher test asserts the exact bug signature (`{'goal'` substring) that Task 3's fix removes.
