# Dynamic Agent Command And Interactive Entrypoint Implementation Plan

**Date:** 2026-06-18  
**Spec:** `docs/superpowers/specs/2026-06-18-dynamic-agent-command-interactive-entrypoint-design.md`
**Release:** Modi Harness v0.7.1

## Outcome

An installed user launches a discovered Agent with `modi <agent-name>`. Agents
that opt into interactive startup collect their own required input through a
checkpointed, client-neutral `request_user_input` protocol. The CLI contains no
Research Assistant-specific branching.

## M0 — Interaction Declaration

Files:

- `src/modi_harness/types.py`
- `src/modi_harness/api/agent.py`
- `src/modi_harness/agents/loader.py`
- `src/modi_harness/api/_session_helpers.py`
- tests under `tests/api/`

Tasks:

- Add `InteractionProtocolConfig(startup="prompt" | "agent")`.
- Parse strict `interaction_protocol` Agent frontmatter.
- Project normalized settings into Agent profile metadata.
- Preserve existing Agents with `startup="prompt"`.

Exit gate: declaration round-trips through Markdown, discovery factory, and
profile projection.

## M1 — Native User Input Protocol

Files:

- add `src/modi_harness/graph/interaction_protocol.py`
- modify `src/modi_harness/graph/nodes.py`
- modify `src/modi_harness/graph/harness_adapter.py`
- modify `src/modi_harness/types.py`
- tests under `tests/graph/`

Tasks:

- Expose `request_user_input` only to opted-in Agents.
- Validate text, multiline, URL-list, confirm, choices, defaults, and field ids.
- Intercept the protocol call before ToolGateway execution.
- Store `PendingInteraction(kind="user_input")` and checkpoint before interrupt.
- Generalize the interaction node to resume submitted values or cancel.
- Emit `interaction_requested` and `interaction_resolved` without renderer data.
- Ensure stale ids and invalid responses do not mutate active state.

Exit gate: scripted model can request multiple inputs, resume on one thread,
then create and complete a native task plan.

## M2 — Generic Client Prompt

Files:

- `src/modi_harness/cli/prompt.py`
- `src/modi_harness/cli/runner.py`
- `src/modi_harness/cli/renderer.py`
- tests under `tests/cli/`

Tasks:

- Add interaction dispatch by `PendingInteraction.kind`.
- Render one-line text, multiline, URL-list, confirm, and choices.
- Map `/cancel` and EOF to cancellation.
- Keep plan review and policy approval behavior unchanged.
- Preserve plain and JSONL event boundaries.

Exit gate: CLI tests cover repeated user inputs, validation feedback,
cancellation, and transition from startup input to plan review.

## M3 — Dynamic Agent Command

Files:

- `src/modi_harness/__main__.py`
- `src/modi_harness/discovery/registry.py`
- tests under `tests/cli/` and `tests/discovery/`

Tasks:

- Pre-dispatch reserved commands before argparse static parsing.
- Treat every other first token as an Agent registry query.
- Support qualified dynamic names and optional trailing initial text.
- Start opted-in Agents with a neutral interactive-startup signal.
- Retain compact prompt fallback for ordinary Agents.
- Refuse empty interactive startup in non-TTY environments.
- Add close-name diagnostics for unknown Agents.
- Keep `modi run NAME --task` compatibility but remove it from primary help.

Exit gate: `modi research-assistant` resolves dynamically; static commands and
legacy automation tests remain green.

## M4 — Research Assistant Migration

Files:

- `agents/research_assistant/agent.md`
- mirrored compatibility example only where still required
- integration tests

Tasks:

- Enable `interaction_protocol.startup = agent`.
- Make first action request URL-list input.
- Ask the model-generated research question through confirm/revision input.
- Continue into existing native task-plan review.
- Assert no URL/question logic exists in generic CLI modules.

Exit gate: scripted end-to-end run performs URL collection, question
confirmation, plan review, task progress, and output submission.

## M5 — Documentation And Validation

Files:

- `README.md`
- `docs/cli.md`
- `docs/agent-discovery-and-task-protocol.md`
- `CHANGELOG.md`

Tasks:

- Make `modi research-assistant` the primary quick start.
- Move task JSON documentation to the automation section.
- Document reserved commands, qualified collision escape, and non-TTY behavior.
- Run full pytest, focused Ruff, targeted mypy, lock check, and diff check.

Exit gate: installed CLI help and docs expose the human command first; all
existing compatibility behavior is tested.
