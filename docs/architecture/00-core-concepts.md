# Core Concepts

Modi Harness uses eight core concepts. Four are the primary mental model:

```text
Workspace
Context
Memory
Trace
```

Four explain execution and persistence:

```text
Session
Thread
Run
Store
```

The rule of thumb:

```text
Workspace = where related work belongs
Session   = how runtime components are bound
Thread    = which runs continue the same task
Run       = one execution attempt
Store     = how durable objects are persisted
Context   = what the model sees for one step
Memory    = what future runs may reuse
Trace     = what happened
```

## Workspace

Workspace is the work boundary.

It answers: where does this related work belong?

Examples:

- a code repository or worktree
- a research topic folder
- a customer support case
- a data analysis package
- an application-defined work unit

Workspace is not limited to coding. It is also not one run's scratch directory.

How it is produced:

- Preferred: the user or application explicitly provides it.
- Fallback: if no workspace is provided, Harness may create a temporary
  workspace under the current directory:

```text
./.modi/workspaces/<workspace_id>/
```

How it is used:

- bounds file access
- owns Harness run files under `.modi/runs/`
- scopes workspace-level memory
- groups work that should share durable context

Temporary workspace rules:

- no approval is required when creating only Harness-managed files under
  `./.modi/`
- the chosen path must be visible in output or trace
- workspace-scoped memory should be disabled unless the caller promotes the
  temporary workspace to an explicit workspace

## Session

Session is the runtime container.

It answers: which agents, model, tools, policy, store, and workspace binding are
assembled right now?

How it is produced:

- application/developer creates it through the SDK
- the model does not create sessions

How it is used:

- starts runs
- resumes threads
- calls the model
- executes tools
- accesses memory, run files, and trace through configured stores

Session is not a business object and should not define memory semantics.

## Thread

Thread is continuity across runs.

It answers: which runs belong to the same continuing task?

How it is produced:

- application provides a thread id for continuation, or
- Harness generates one for a new task

How it is used:

- groups retries, resumes, follow-ups, and delegated work
- scopes thread-level memory
- lets APIs fetch trace, artifacts, and state for a continuing task

Thread is preferred over the older term `conversation` because Modi Harness is
not limited to chat workflows.

## Run

Run is one execution attempt.

It answers: what happened from the start to the terminal status of this
execution?

How it is produced:

- Session/runtime creates a run for each execution
- child agents may create child runs

How it is used:

- owns runtime state
- owns run files
- emits trace events
- builds one or more contexts
- ends with a status

Run files live under the workspace:

```text
<workspace>/.modi/runs/<run_id>/
  input/
  state/
  refs/
  drafts/
  artifacts/
  logs/
```

`draft`, `artifact`, `ref`, and `log` are run-file roles. They are not top-level
architecture concepts.

## Store

Store is the persistence substrate.

It answers: how are durable objects saved and read back?

How it is produced:

- application/session configuration selects a backend
- local filesystem is the default implementation

How it is used:

- persists run files
- persists memory records
- persists trace files
- persists checkpoints/state snapshots

Store is an implementation capability, not the user's primary mental model.

## Context

Context is model input for one step.

It answers: what can the model see now?

How it is produced:

- runtime assembles it before each model call
- Context Manager produces a provider-independent `ContextPack`
- Model Adapter converts the pack to provider messages

How it is used:

- passed to the model
- hashed for trace/debugging
- discarded after the step unless explicitly recorded for debugging

Context may include:

- instructions
- current task
- recent messages
- selected memory
- selected workspace references
- tool descriptions
- output contract

Context must not blindly include all memory, all workspace files, or trace.

## Memory

Memory is compact reusable knowledge for future context.

It answers: should future runs possibly see this again?

How it is produced:

- user/application explicitly writes it
- model calls `propose_memory`, and policy accepts it
- a future summarizer may create it from run outputs, but that is not a core
  requirement

How it is used:

- selected into future context when relevant
- searched by model-facing `recall_memory`
- updated, expired, superseded, or deleted by explicit memory operations

Memory should be small. It should hold preferences, rules, feedback, reusable
methods, or pointers. It should not hold trace events, drafts, full artifacts,
raw webpages, or complete task outputs.

Preferred conceptual scopes:

```text
user       cross-workspace user preference
workspace current work boundary facts or rules
agent      reusable method for one agent
thread     short-term continuity inside one task chain
```

Compatibility note: current code still uses `project` for workspace scope and
`conversation` for thread scope. New docs should prefer `workspace` and
`thread`; code should migrate gradually with aliases.

## Trace

Trace is append-only event history.

It answers: what happened?

How it is produced:

- runtime emits trace events during a run
- model and tools do not directly author trace

How it is used:

- debugging
- audit
- replay
- explaining decisions
- evaluating scenarios

Trace records events such as:

- run start/end
- context built
- model call/result
- tool call/result
- policy decision
- memory selected/written
- error

Trace is not memory. Trace does not enter context by default. Applications may
summarize trace into memory, but that must be explicit.

## Decision Table

| Question | Concept |
|---|---|
| Where does this work belong? | Workspace |
| Which components are bound together? | Session |
| Does this continue the same task chain? | Thread |
| Is this one execution attempt? | Run |
| How is it persisted? | Store |
| What does the model see now? | Context |
| Should future runs reuse this? | Memory |
| What happened? | Trace |

## Naming Guidance

Preferred new conceptual names:

| Legacy/current wording | Preferred concept |
|---|---|
| `project` | `workspace` |
| `conversation` | `thread` |
| per-run workspace directory | run files / run store |
| `WorkspaceManager` conceptually | run files manager |
| `workspace_root` when used as run storage | run store root |
| `project_root` for hooks | hook root or workspace root, depending on use |

V0.6.b is documentation-first. Public API renames should happen only in a later
compatibility-planned release.
