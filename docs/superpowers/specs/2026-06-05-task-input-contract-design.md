# run_task Input Contract — Formalization (Design)

**Status:** Design approved 2026-06-05. Awaiting implementation plan.
**Type:** Code (new `TaskInput` type + shared helper, unify 3 call sites, 1 behavior addition) + docs (3 files) + tests.
**Motivation:** `ModiSession.run_task(input=...)` accepts a free-form `dict[str, Any]`
whose real contract — *which keys are recognized and in what precedence the
agent's first user message is derived* — is encoded only inside a private
function, `_input_to_user_text()` (`graph/harness_adapter.py:425`). The contract
is undocumented and inconsistently applied:

- **No docs describe it.** `types-reference.md` (the self-declared authoritative
  type source) types `input` as a bare `dict`. Every example shows
  `{"goal": "...", "messages": [...]}` without explaining that `goal` is
  optional, that `messages` takes precedence, or that `tags`/`reference_keys`
  steer memory selection.
- **`docs/cli.md:28-33` is wrong.** It instructs `echo '{"prompt": "hello"}'`,
  but `prompt` is not a recognized key — it falls through to the
  `str(payload)` branch, so the model receives the literal string
  `{'prompt': 'hello'}` instead of `hello`.
- **`subagent/dispatcher.py:113` is a latent bug.** It builds a delegated
  subagent's first message with `content=str(child_input)` — it never calls the
  parser at all, so every delegated subagent receives a stringified dict
  (e.g. `{'goal': 'write report'}`) as its user message.

These are three symptoms of one root cause: the input contract is implicit,
private, and duplicated. This design makes it explicit, shared, and documented.

---

## 1. Goal

Promote the `run_task` input contract from an implicit private function to a
first-class, documented, single-source-of-truth contract, and fix the two
places where the absence of that contract caused incorrect behavior.

**In scope:**

- A `TaskInput` TypedDict in `types.py` declaring the recognized keys.
- A public, documented helper `task_input_to_text()` in `_utils/`, replacing the
  private `_input_to_user_text()`.
- One behavior addition: `prompt` becomes a recognized key (lowest-risk fix for
  the `cli.md` example — adds a key, never reorders existing precedence).
- Unify all three call sites on the shared helper (fixes the dispatcher bug).
- Document the contract in `08-harness-api.md`, `types-reference.md §15`, and
  fix `cli.md`.
- Tests covering precedence, every recognized key, the fallback, and the
  dispatcher fix.

**Explicitly out of scope:**

- A full "input router" / multi-modal input system (tracked separately in
  `docs/architecture/future/input-router.md`). This design formalizes only the
  *existing* contract; it does not expand what inputs mean.
- Validating or rejecting unrecognized keys. `input` stays an open dict;
  `TaskInput` is `total=False` and documents recognized keys without forbidding
  extras.
- Changing the precedence of any *currently-recognized* key.
- Reworking how `tags`/`reference_keys` drive memory selection (only documenting
  the existing behavior).

## 2. The Contract (authoritative)

The agent's first user message is derived from the `input` payload by the
following precedence. The first rule that matches wins:

| Priority | Key | Rule |
|---|---|---|
| 1 | `messages` | If a non-empty `list`, use the `content` of the last item with `role == "user"`. |
| 2 | `prompt` | **(new)** Use as the message text. |
| 3 | `customer_message` | Use as the message text. |
| 4 | `question` | Use as the message text. |
| 5 | `goal` | Use as the message text. |
| 6 | *(fallback)* | `str(payload)` — the whole dict stringified. |

Two further keys are read elsewhere (by memory selection in
`memory/store.py:select_for_context`), and are part of the documented contract
even though they do not affect the first message:

- `tags: list[str]` — filters project-scope memory records.
- `reference_keys: list[str]` — selects reference-scope memory records by name.

**`messages` precedence note:** if `messages` is present but contains no
`role == "user"` item, rule 1 does not match and evaluation continues to rule 2.
This preserves current behavior (`_input_to_user_text` returns from the
`messages` branch only when a user message is found).

## 3. Code Changes

### 3.1 `TaskInput` type (`src/modi_harness/types.py`)

Add alongside the existing TypedDicts:

```python
class TaskInput(TypedDict, total=False):
    """Input payload for ModiSession.run_task / stream / astream.

    All keys are optional and the dict may carry additional keys the agent
    expects. The harness derives the agent's first user message from these
    keys in priority order: messages > prompt > customer_message > question
    > goal, falling back to str(payload). `tags` and `reference_keys`
    additionally steer memory selection. See docs/architecture/08-harness-api.md.
    """
    messages: list[Message]
    prompt: str
    customer_message: str
    question: str
    goal: str
    tags: list[str]
    reference_keys: list[str]
```

`total=False` means call sites passing a plain `dict[str, Any]` (with or without
extra keys) remain valid. The execution method signatures keep
`input: dict[str, Any]` — `TaskInput` documents the recognized shape without
narrowing the accepted type, so this is non-breaking.

### 3.2 Shared helper (`src/modi_harness/_utils/task_input.py`)

New module, exported from `_utils/__init__.py`:

```python
def task_input_to_text(payload: Mapping[str, Any]) -> str:
    """Derive the agent's first user message from a TaskInput payload.

    Precedence: messages (last role=="user") > prompt > customer_message
    > question > goal > str(payload). See docs/architecture/08-harness-api.md.
    """
```

Body is the current `_input_to_user_text` logic with `"prompt"` inserted into the
recognized-key tuple immediately after the `messages` branch:
`("prompt", "customer_message", "question", "goal")`.

**Placement rationale:** `_utils` is a leaf package (imports only stdlib);
`types.py` imports only stdlib; both `graph/harness_adapter.py` and
`subagent/dispatcher.py` already import from `_utils`. Placing the helper here
lets both call sites share it with no new or circular imports.

### 3.3 Unify call sites

| File | Before | After |
|---|---|---|
| `graph/harness_adapter.py:347` | `_input_to_user_text(request.input)` | `task_input_to_text(request.input)` |
| `graph/harness_adapter.py:425-433` | private `_input_to_user_text` def | **deleted** |
| `subagent/dispatcher.py:113` | `content=str(child_input)` | `content=task_input_to_text(child_input)` |

The CLI path (`cli/runner.py`, `__main__.py`) flows `input` through `run_task`
unchanged, so it is fixed transitively once `prompt` is recognized.

## 4. Documentation Changes

1. **`docs/architecture/08-harness-api.md`** — under the Execution section, add a
   "TaskInput payload" subsection containing the §2 precedence table and the
   `tags`/`reference_keys` notes. Replace the bare
   `input={"goal": "...", "messages": [...]}` example with one that shows the
   keys *and* explains that `messages` wins and `goal` is an optional fallback.
2. **`docs/types-reference.md §15 (Harness API Types)`** — add the `TaskInput`
   TypedDict to the code block, so the authoritative type source no longer says
   only `input: dict`. Cross-reference `08-harness-api.md` for precedence.
3. **`docs/cli.md:28-33`** — the `{"prompt": "hello"}` example becomes correct
   once `prompt` is recognized. Add a one-line pointer to the recognized-key set
   (link to `08-harness-api.md`) so the accepted shape is not implicit.

## 5. Testing

**`task_input_to_text` (new unit test):**

- Each recognized key in isolation maps to the expected text:
  `messages`, `prompt`, `customer_message`, `question`, `goal`.
- Precedence: a payload carrying all five resolves to the `messages` user text;
  removing `messages` resolves to `prompt`; and so on down the chain.
- `messages` present but with no `role == "user"` item falls through to the next
  key (precedence note in §2).
- Empty / unrecognized-only payload falls back to `str(payload)`.

**Dispatcher fix (new test):**

- A delegated subagent's seeded first message equals
  `task_input_to_text(child_input)` and is **not** `str(child_input)` — locks
  the `dispatcher.py:113` bug fix. Concretely: delegating with
  `task={"goal": "write report"}` yields a first message of `"write report"`,
  not `"{'goal': 'write report'}"`.

**Regression guard:**

- Existing top-level `run_task` behavior is unchanged for inputs that already
  worked (`messages`-based and `goal`-based), confirming the refactor is
  behavior-preserving except for the additive `prompt` key.

## 6. Risks and Mitigations

- **Behavior change from recognizing `prompt`:** purely additive — a payload
  with `prompt` previously hit the `str(payload)` fallback; now it resolves
  correctly. No previously-recognized key changes meaning. Any caller that
  somehow *relied* on `{"prompt": ...}` stringifying to the whole dict (no known
  caller; the only references are the broken doc example) would change. Accepted.
- **Dispatcher fix changes delegated-subagent inputs:** delegated subagents now
  receive the derived text instead of a stringified dict. This is the intended
  fix; the new test pins it. Any agent prompt that happened to depend on seeing
  the dict literal would change — none are known.
- **Type vs. runtime drift:** `TaskInput` is documentation-grade (`total=False`,
  not enforced at runtime). The precedence table in §2 and the helper docstring
  must stay in sync with `task_input_to_text`. Mitigation: the helper docstring
  links to `08-harness-api.md`, and the unit tests encode the precedence so
  drift breaks CI.
