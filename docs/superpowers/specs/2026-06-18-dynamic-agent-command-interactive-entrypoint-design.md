# Dynamic Agent Command And Interactive Entrypoint Design

**Date:** 2026-06-18  
**Target:** Modi Harness v0.7 follow-up  
**Status:** Proposed

## 1. Problem

The v0.7 discovery runtime makes Agents addressable by name, but the primary CLI
still exposes implementation details:

```text
uv run modi run research-assistant
Task for research-assistant
>
```

This is the wrong human interface. `uv run` is a repository-development detail,
`run` is a Harness operation, and `task.json` is an automation transport. A user
who selects Research Assistant expects the assistant to start and collect the
information it needs.

The CLI must not solve this by hard-coding URL prompts or importing an
Agent-specific wizard. That would undo the separation established in v0.7 and
would not transfer to Web, Desktop, or API clients.

## 2. Decision

Agent names become dynamic top-level commands:

```bash
modi research-assistant
modi code-auditor
modi support-bot
```

The CLI discovers and resolves the first positional token through the existing
`AgentRegistry`. After resolution, the Agent drives startup through the native
interaction protocol.

`modi run NAME --task ...` remains a compatibility and automation surface. It
is removed from the primary human quick start and help examples.

## 3. Command Resolution

### 3.1 Reserved commands

The following names are reserved by the CLI:

- `agents`
- `info`
- `plugins`
- `resume`
- `run`
- `help`

Before constructing the argparse command tree, the CLI examines the first
non-option token. Reserved names follow the static command parser. Any other
token is treated as an Agent query.

### 3.2 Dynamic Agent query

The token is resolved exactly like `modi agents which`:

- an exact qualified name resolves directly;
- an unqualified unique name resolves;
- ambiguity fails and lists qualified candidates;
- an unknown name fails with close discovered names and a hint to run
  `modi agents list`.

No dynamic argparse subparser is generated for every Agent. Resolution remains
registry-driven, so installed plugins and newly added project Agents appear
without regenerating CLI code.

### 3.3 Name collisions

An Agent whose name collides with a reserved command cannot use the unqualified
top-level form. It remains addressable through its qualified dynamic name:

```bash
modi project:agents
```

Reserved command behavior never changes based on discovered packages.

## 4. Interactive Startup Protocol

### 4.1 New native tool

The Harness adds a renderer-independent protocol tool:

```text
request_user_input
```

Its arguments contain:

- `prompt`: the question presented to the user;
- `input_type`: `text`, `multiline`, `url_list`, or `confirm`;
- `required`: whether an empty response is valid;
- optional `default` and bounded `choices`;
- optional stable `field` identifier for the Agent's own reasoning context.

The tool does not read stdin. It writes a `PendingInteraction(kind="user_input")`
to checkpoint state and routes to the existing interaction node. Clients render
and answer the interaction through the same thread.

### 4.2 Response

Clients resume with:

```json
{
  "interaction_id": "...",
  "decision": "submitted",
  "value": "..."
}
```

For `url_list`, `value` is a list of validated non-empty strings. `/cancel` maps
to `decision="cancelled"`. The resulting tool message is returned to the model,
which may request another input or begin planning.

### 4.3 Entrypoint declaration

Interactive startup is opt-in Agent metadata:

```yaml
interaction_protocol:
  startup: agent
```

When `modi <agent-name>` launches such an Agent without an initial request, the
Harness seeds a neutral startup signal rather than inventing a user task. The
Agent instruction defines which input it asks for first.

Agents without interactive startup retain a compact generic prompt. A dynamic
command may also accept trailing text:

```bash
modi support-bot "My invoice is incorrect"
```

This becomes the initial user request and skips the empty-start signal.

## 5. Research Assistant Experience

Research Assistant declares interactive startup. Its instruction requires:

1. request one or more research URLs with `request_user_input(input_type="url_list")`;
2. inspect the supplied URLs sufficiently to propose a natural research question;
3. request confirmation or revision of that question;
4. create the native task plan;
5. wait for plan review;
6. execute with truthful task transitions.

Expected entry:

```text
$ modi research-assistant

Research Assistant

请输入研究 URL，每行一个；空行结束。
> https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
>

正在基于来源生成研究问题...
```

There is no Research Assistant branch in the CLI runner.

## 6. Client Responsibilities

Harness owns:

- interaction state and validation;
- checkpoint and resume semantics;
- canonical interaction events;
- cancellation status;
- protocol tool visibility.

Agent owns:

- what information is required;
- question wording and sequence;
- domain validation or clarification;
- when enough input exists to plan.

Client owns:

- rendering text, multiline, URL-list, choice, and confirmation controls;
- collecting a response;
- resuming with the interaction id;
- accessibility and terminal-specific behavior.

## 7. CLI Rendering

The generic interaction prompt supports:

- `text`: one line;
- `multiline`: repeated lines, empty line submits;
- `url_list`: repeated lines with lightweight URL syntax feedback;
- `confirm`: Enter accepts the default, text requests revision when allowed;
- `/cancel`: cancels any interaction.

The live task renderer closes before input and resumes after submission. Plain
mode emits stable prompt/response boundary lines. JSONL mode emits the
interaction event and does not attempt to read stdin unless explicitly running
interactively.

## 8. Non-Interactive Behavior

`modi <agent-name>` requires a TTY when the Agent must collect startup input. In
a pipe or CI environment, the command fails with a concise instruction to use
the API or compatibility automation form.

`modi run NAME --task -` and `ModiSession.run_task(...)` remain supported for
automation. `task.json` is documented only in the automation/API section.

## 9. Errors

- Missing Agent: show close discovered names and `modi agents list`.
- Ambiguous Agent: list qualified candidates.
- Missing model configuration: show required `MODI_MODEL_*` variables.
- EOF during required input: cancel cleanly without traceback.
- Invalid URL line: keep collecting and show one local validation message.
- Stale interaction id: reject without mutating checkpoint state.
- Unsupported interaction type: fail visibly as a client capability error.

## 10. Compatibility

- Existing static commands remain unchanged.
- Existing `modi run` invocations continue to work.
- Agents without `interaction_protocol` behave as before.
- Policy approvals and plan reviews retain their current payloads.
- `PendingInteraction` is extended additively for typed user input.

## 11. Verification

Tests must cover:

- dynamic resolution from project, plugin, user, explicit, and qualified names;
- reserved-command collisions;
- trailing-text initial requests;
- empty interactive startup and repeated user-input interactions;
- multiline and URL-list collection;
- cancellation and EOF;
- non-TTY refusal with an actionable message;
- plan review after startup interactions;
- Research Assistant end-to-end flow with no Agent-specific CLI code;
- unchanged `modi run NAME --task` compatibility.

## 12. Success Criteria

From an installed environment, a user can type:

```bash
modi research-assistant
```

and immediately enter a domain-appropriate conversation. The same Agent can run
behind another client by consuming and responding to canonical interactions,
without importing CLI code or duplicating startup logic.
