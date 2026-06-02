# Permissions, Interaction, and Execution

This document is the single source of truth for how modi-harness decides whether a tool call runs, who gets asked when the answer is uncertain, and what "running" actually means.

It supersedes the conceptual content in `06-policy-gate.md` (which now describes only the gate's implementation contract) and `14-permission-mode.md` (which described an earlier 4-mode design and will be retired once the rename in §8 ships).

> **Status.** §1–§4 describe the **target model**. The current code uses the legacy names (`permission_mode`, `ask`/`auto`/`plan`/`bypass`) and lacks the `settings.permissions` section. §8 lists the migration. Until the rename ships, the legacy names map onto the new model as described in §8.

---

## 1. The conceptual split

Three things have been collapsed into one knob in the past, and that's the source of every confusion in this area:

| Concern | What it answers | Who declares it |
|---|---|---|
| **Permission** | "Is this action allowed?" | Tool author + agent author + operator + user — four layers, evaluated in order |
| **Interaction** | "When the answer is 'ask a human', how do we ask?" | Operator picks at runtime, but framework auto-detects TTY |
| **Execution** | "When we do run the action, do we run for real or simulate?" | Operator picks at runtime |

The framework keeps these three concerns separate internally. The product surface collapses them into one `mode` knob because users should not have to compose three orthogonal axes in their head.

---

## 2. Permission — the four layers

Every tool call passes through these four layers. Any layer can deny. An action runs only if every layer either allows or stays silent.

```
ToolSpec.risk_level    →  Agent permission_profile  →  Rule packs  →  User settings.json
  (tool author)             (agent author)              (operator)      (user)
```

### 2.1 Tool risk level

Declared statically in the tool's `ToolSpec.risk_level`. Five values:

| Risk | Meaning | Examples |
|---|---|---|
| `L0` | Pure compute, no side effects | `list_workspace_dir`, `read_workspace_file`, `recall_memory`, `fetch_url` |
| `L1` | Local writes inside the run's workspace | `save_draft`, `save_artifact`, `save_memory` |
| `L2` | Writes that escape the workspace but stay inside the user's machine | edit a project file, write to `~/.config` |
| `L3` | Business writes — durable, externally observable, hard to undo | DB write, ticket creation, repo push |
| `L4` | High-impact external action with monetary, security, or broadcast consequences | send mass email, charge a card, post to a public channel |

This is the only layer the tool **author** owns. Everything below modifies how the framework treats a given risk level.

### 2.2 Agent permission profile

Declared per-agent in `agent.md` frontmatter. Three lists:

```yaml
permission_profile:
  preauthorized:    # tools the agent can use silently regardless of risk
    - fetch_url
  review_required:  # tools that must always go through human review
    - send_email
  deny:             # tools the agent is never allowed to call
    - shell_exec
```

This layer narrows what a specific agent can do. It cannot widen — `deny` always wins over a permissive setting elsewhere.

### 2.3 Rule packs

Operator-level matchers loaded by `MODI_POLICY_RULE_PACKS`. Built-in packs:

| Pack | What it covers |
|---|---|
| `core` (always on) | denied-retry, destructive-without-authorization, security abuse |
| `coding` | git mutations, package management, infra config |
| `messaging` | external messaging, broadcast |
| `finance` | monetary writes, payment APIs |

Rule packs can only **elevate** — turn an `allow` into `require_approval`, or `require_approval` into `deny`. They cannot lower a decision.

### 2.4 User settings — `permissions`

The user's standing preferences, in `~/.modi/settings.json` and `.modi/settings.json` (project file is unioned with user, project entries first). Three lists:

```json
{
  "permissions": {
    "always_allow": ["save_draft", "save_artifact", "L1"],
    "always_deny":  ["send_email_blast"],
    "always_ask":   ["L3", "L4"]
  }
}
```

Each entry is either a tool name (`save_draft`) or a risk-level token (`L0`..`L4`); both routes are equivalent — the gate considers a (tool, risk) pair matched if **either** appears in a list.

Priority within this layer is `deny > ask > allow`: if the same tool appears on more than one list, `always_deny` wins, then `always_ask`, then `always_allow`. `always_allow` only takes effect when the base risk/mode decision would otherwise gate the call; it cannot override a hard agent `deny` (§2.2) or a `core` rule pack `deny` (§2.3) — those run before this layer.

This is the layer the **user** owns; it is the one knob that lets a user override both the tool author and the agent author without writing hook scripts. The legacy `hooks` settings (pre/post-tool-use scripts that can `block` or `approve`) remain available for cases that need full programmatic control. `permissions` is the declarative shortcut for the 95% case.

### 2.5 Composition

The four layers compose to produce one of three permission outcomes:

| Outcome | Meaning |
|---|---|
| `allow` | Run the tool. |
| `require_human` | A human must decide. What happens next is decided by the **interaction** axis (§3). |
| `deny` | Never run, never ask. Recorded in trace. |

The composition rule is monotone: any `deny` from any layer wins; otherwise the strictest non-deny outcome wins.

---

## 3. Interaction — what "require_human" means at runtime

When the permission layers say `require_human`, the framework needs to decide whether to actually interrupt and ask, or to fail closed.

| State | Behavior |
|---|---|
| TTY attached and `MODI_INTERACTIVE` not set to `0` | Interrupt the run, prompt the user, resume on their decision |
| No TTY (CI, batch, daemonized) **or** `MODI_INTERACTIVE=0` | Auto-deny, record `denied_action`, run continues |

The user does **not** pick this — the framework auto-detects. `MODI_INTERACTIVE=0` is the escape hatch for users running long jobs in tmux/screen who want strict deny-on-uncertain behavior despite having a terminal.

For asynchronous approval flows (Slack webhook, ticketing system), use a `pre_tool_use` hook that intercepts `require_human` and translates to an external workflow. This is a hook concern, not a mode concern.

---

## 4. Execution — what "run" actually means

Once we've decided to run a tool, two execution semantics are possible:

| Semantic | What happens for L0 | What happens for L1+ |
|---|---|---|
| `live` | Run the real handler | Run the real handler |
| `dry-run` | Run the real handler (reads have no side effects) | Run the tool's `dry_run` handler if declared, else **intercept**: return synthetic `{ok: true, dry_run: true}` and record the would-be call in trace |

The intercept rule for `dry-run` matters. Without it, `dry-run` would be useless — most tools don't bother to implement a `dry_run` handler, so the agent would crash on its first L1 call. With intercept, the agent runs end-to-end believing it succeeded, and the operator gets a clean trace of what *would* have happened. The intercept marker (`dry_run: true` on the tool result, plus a `simulated: true` flag in the trace) makes the simulation explicit and audit-friendly.

---

## 5. The product surface — three modes

The four-layer permission system × two interaction states × two execution semantics produces eight theoretical combinations. Only four are real:

| permission | interaction | execution | Real? | Mapped to |
|---|---|---|---|---|
| standard | ask | live | ✓ | `auto` (TTY) |
| standard | deny | live | ✓ | `auto` (no TTY) |
| standard | deny | dry | ✓ | `preview` |
| skip | — | live | ✓ | `trust` |
| (other 4) | | | ✗ | nonsensical |

The product surface compresses these four into **three modes**, because the difference between TTY and no-TTY shouldn't be a user-visible choice — it should be auto-detected.

| `mode` | Story | permission | interaction | execution |
|---|---|---|---|---|
| `auto` (default) | "Apply the rules. Ask me when needed, deny when I'm not around." | standard layers | ask if TTY, deny if not | live |
| `preview` | "Show me what would happen, but don't touch anything." | standard layers | deny silently | dry-run / intercept |
| `trust` | "Disable the gate. Dev only." | skip all layers | n/a | live |

### 5.1 Mode boundaries

| | `auto` vs `preview` | `auto` vs `trust` | `preview` vs `trust` |
|---|---|---|---|
| permission | same | different (standard vs skip) | different |
| interaction | different (adaptive vs always-deny) | different (adaptive vs n/a) | different |
| execution | different (live vs dry) | same | different |

Each pair differs on at least two axes. The three modes are disjoint by construction; no edge case lands ambiguously between two modes.

### 5.2 Selection

```
Resolution order (first match wins):

1. RunTaskRequest.mode          (per-run override)
2. Agent permission_profile.mode (per-agent default)
3. Settings.MODI_MODE             (deployment default)
4. Fallback: auto
```

Inside `auto`, TTY detection is automatic but can be forced off with `MODI_INTERACTIVE=0`.

`trust` mode requires `MODI_ALLOW_TRUST=1` to be settable. This is a guard against accidentally shipping a config with `trust` to production. Without the env var, `mode=trust` raises a startup error.

### 5.3 Mode changes mid-run

- A run cannot self-elevate its mode. Only the harness API caller can change mode at `resume_task`.
- Downgrading (`auto` → `preview`) is always allowed.
- Upgrading (`auto` → `trust`, `preview` → anything) requires explicit caller intent and is recorded as a trace event.

---

## 6. Putting it together — what happens for one tool call

Trace through one tool call from proposal to outcome:

```
Model proposes  fetch_url(url=…)
                       │
                       ▼
1. ToolSpec lookup           → risk=L0
2. Schema validation         → ok
3. Denied-retry check        → not denied
4. Pre-hooks                 → no block
5. Permission composition
   ├─ Tool risk        L0   →  candidate=allow
   ├─ Agent profile    silent →  candidate=allow
   ├─ Rule packs       silent →  candidate=allow
   └─ User permissions silent →  outcome=allow
6. Mode dispatch
   ├─ outcome=allow      →  proceed to execution
   ├─ outcome=require_human →  interaction step (if mode=auto: ask if TTY else deny;
   │                                              if mode=preview: deny silent;
   │                                              if mode=trust: skipped earlier)
   └─ outcome=deny       →  record denial, return error
7. Execution
   ├─ mode in (auto, trust): run live handler
   └─ mode = preview        : run dry-run handler or intercept
8. Post-hooks
9. Trace event with full audit
```

Step 5 (permission composition) is mode-independent — it's the same four-layer evaluation regardless of mode. Step 6 is where mode matters.

---

## 7. Edge cases and their standard answers

### 7.1 TTY attached but I want non-interactive

`MODI_INTERACTIVE=0`. This stays inside `mode=auto` — it just disables the TTY detection. Useful for long-running screen/tmux sessions.

### 7.2 No TTY but I want async approval

Don't use a different mode. Write a `pre_tool_use` hook that intercepts `require_human` and sends a Slack/webhook approval request, then resumes the run when approval comes back. The hook system is the right escape hatch for async approval workflows.

### 7.3 I want "L1 silent allow, L2+ ask" without writing hooks

Use `settings.permissions`:

```json
{ "permissions": { "always_allow": ["L1"], "always_ask": ["L2", "L3", "L4"] } }
```

This narrows permission outcomes before mode dispatch. In `auto` with TTY, L2+ now prompts; without TTY, L2+ denies.

### 7.4 trust is too dangerous to ship in our prod image

Don't set `MODI_ALLOW_TRUST=1` in the production environment. Without it, any attempt to use `mode=trust` fails fast at startup. This is the only place where there's a hard env-var prerequisite.

### 7.5 The model keeps proposing the same denied action

The denied-retry guard (in `core` rule pack) blocks an action whose fingerprint matches a prior denial in the same run. This is mode-independent and cannot be turned off.

---

## 8. Migration — current names → target names

The current code uses the legacy 4-mode design. The migration to the 3-mode product surface will land in a future release.

| Legacy | Target | Notes |
|---|---|---|
| `permission_mode` | `mode` | The knob is renamed; the field name in `RunTaskRequest`, `agent.md`, settings, and env var all change. |
| `ask` | `auto` (TTY default) | When TTY is attached, `auto` behaves like the old `ask`. |
| `auto` | `auto` (no-TTY behavior) | When no TTY, `auto` behaves like the old `auto` did with preauthorized lists. |
| `plan` | `preview` | Renamed for clarity (no overlap with planning agents) and re-defined to use the intercept rule (§4) so non-dry_run-aware tools work too. |
| `bypass` | `trust` | Renamed; gains `MODI_ALLOW_TRUST=1` startup guard. |

During the transition, the old names continue to work as deprecation aliases for one minor release, with a warning emitted on use.

---

## 9. What this document does not cover

- **The PolicyGate's internal contract** — see `06-policy-gate.md`.
- **The Tool Gateway's dispatch pipeline** — see `05-tool-gateway.md`.
- **The hook system** — see `13-hook-system.md`.
- **Risk-level rationale and examples** — see `types-reference.md`.
- **How to write a rule pack** — to be documented when the rule pack API becomes public.
