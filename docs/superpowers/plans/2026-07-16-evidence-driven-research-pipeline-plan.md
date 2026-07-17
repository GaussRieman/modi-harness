# Evidence-Driven Research Pipeline Implementation Plan

# Evidence-Driven Research Pipeline Implementation Plan

> **As-built note:** implementation is complete; all 675 tests pass, Ruff and
> mypy are clean, `git diff --stat -- src/modi_harness/` is empty. Two steps
> below (2 and part of 1) deviated from the original plan during
> implementation — see the "Deviations" section in the companion design doc
> (`docs/superpowers/specs/2026-07-16-evidence-driven-research-pipeline-design.md`)
> for the full rationale. Steps are left as originally written for the
> history; inline notes mark what actually shipped.

All steps are scoped to `agents/research_assistant/`. No change to
`src/modi_harness/` is expected; if implementation reveals one is genuinely
unavoidable, stop and confirm scope before making it rather than folding it
in silently.

1. Extend `confirm_scope`'s `completion.output_schema` in
   `agents/research_assistant/workflows/deep_research.yaml`: add required
   per-item `verification_method` (5-value enum including
   `unverifiable_flag`) and optional top-level `decision_context` /
   `constraints`.

   **As built:** `decision_context` / `constraints` shipped as planned.
   `verification_method` was **not** added to `confirm_scope`'s TaskPlan item
   schema — `confirm_scope`'s `completion.review: required` sends the
   reviewed TaskPlan through `tasks.py:create_task_plan`, which rebuilds
   every item from a fixed `id`/`title`-only shape before schema validation
   runs, silently dropping any extra field and making the Brain's submission
   unrecoverably rejected. Confirmed with the user; `verification_method`
   moved to being judged by the Brain per task at `investigate` time instead,
   supplied directly on the `record_research_finding` call.

2. Add `public_web_search_batch` to `agents/research_assistant/tools/research.py`:
   task-level parallel dispatch wrapping the existing per-task
   provider-parallel search. Set `max_calls_per_node: 2` on its spec (no
   `max_calls_per_task`, no `task_id` argument). Keep `public_web_search`
   unchanged for `quick_lookup`.

   **As built:** dropped in favor of the simpler v1 scope confirmed with the
   user. `public_web_search` stayed the single search Operation for both
   workflows; only its `max_calls_per_task` changed, `1` -> `2`, so a task
   can issue one follow-up query. No batching Operation was added.

3. Add `verify_claim_evidence` (schema + handler) in the same file:
   pre-filter against observed sources, accept the Brain's
   `supporting|contradicting|unrelated` / `independent|same_origin` /
   `direct|indirect` tags per evidence item, and raise `ValueError` when two
   `independent`-tagged items share a domain (existing generic
   handler-failure-triggers-repair path handles the rest).

4. Add `agents/research_assistant/confidence.py`: pure functions implementing
   the six-factor table and the `min()` combination rule. No I/O, no Runtime
   dependency — takes tagged evidence + `verification_method`, returns
   `high|medium|low`.

5. Update `record_research_finding`'s handler and input schema in
   `agents/research_assistant/tools/research.py`: keep `evidence` as one flat
   array, add `stance`/`independence`/`directness` per item, remove the
   `confidence` input field and compute it via `confidence.py` instead, fold
   verification-method gaps into the existing `limitations` output list.

6. Update `investigate`'s `goal` text and `capabilities.tools` in
   `deep_research.yaml` to include `public_web_search_batch` and
   `verify_claim_evidence`; change its `transitions.completed` from
   `$complete` to `finalize_report`.

   **As built:** `capabilities.tools` is
   `[public_web_search, verify_claim_evidence, record_research_finding]`
   (no batch tool, per step 2). `goal` text also now instructs the Brain to
   choose `verification_method` per task at the start of that task, per
   step 1's deviation.

7. Add the `finalize_report` operation Node to `deep_research.yaml`
   (`inputs.report: {$ref: "#/nodes/investigate/output"}`,
   `capabilities.operations: [build_evidence_graph]`,
   `transitions.completed: $complete`).

   **As built:** uses `operation: build_evidence_graph` directly (the
   `execution: operation` Node shape takes a single `operation:` field, not a
   `capabilities.operations` list — matches the pre-existing pattern already
   used by `quick_lookup`'s `search` Node).

8. Add `build_evidence_graph` Operation (schema + handler) in
   `agents/research_assistant/tools/research.py`: pure function building a
   Mermaid `flowchart` string from `key_findings`, returning the input
   object plus `evidence_graph`.

9. Bind the three new Operations (`public_web_search_batch`,
   `verify_claim_evidence`, `build_evidence_graph`) in
   `agents/research_assistant/agent.py`'s `_TOOL_DEFINITIONS`.

   **As built:** two new Operations bound (`verify_claim_evidence`,
   `build_evidence_graph`); no `public_web_search_batch`, per step 2.

10. Update `agents/research_assistant/skills/web-research/SKILL.md` and the
    Agent instruction in `agent.py`: describe `verification_method`
    selection at scope time, the `unverifiable_flag` short-circuit (record
    `blocked` directly, no search), the two-stage verification protocol, and
    that `confidence`/`evidence_graph` must not be supplied by the model.

    **As built:** describes `verification_method` selection at the start of
    each task in `investigate`, not at scope time, per step 1's deviation.

11. Tests (all under `tests/agents/`, mirroring existing
    `test_research_assistant.py` / `test_research_tools.py` structure):
    - task-level parallelism and the `max_calls_per_node: 2` cap on
      `public_web_search_batch`;
    - `unverifiable_flag` short-circuit records `blocked` without a search
      call;
    - `verify_claim_evidence` pre-filter and domain re-check
      rejection/re-annotation cycle;
    - `confidence.py`'s six-factor table and `min()` combination, including
      the one-bad-factor-caps-everything case, as isolated unit tests with
      no Workflow involved;
    - `record_research_finding` rejects a `confidence` field if the model
      supplies one, and evidence items missing `stance`/`independence`/
      `directness`;
    - `build_evidence_graph` output structurally matches `key_findings`
      (every edge traces to a real evidence item);
    - full regression for `quick_lookup`, CLI rendering, and existing
      `deep_research` TaskPlan/Trace tests (must be unaffected).

    **As built:** the task-level-parallelism/batch-cap bullet was replaced by
    a `max_calls_per_task: 2` assertion on `public_web_search` (no batch
    Operation exists to test). All other bullets shipped as planned;
    `test_research_tools.py` (25 tests) and `test_research_assistant.py`
    (10 tests) both fully green.

12. Run the full test suite, Ruff, mypy, and `git diff --check`; confirm
    `git diff --stat -- src/modi_harness/` is empty. Review and commit.

    **As built:** done — 675 tests pass (full suite), Ruff clean, mypy
    baseline unchanged by this work (pre-existing repo-wide gaps only), and
    `git diff --stat -- src/modi_harness/` is empty.

13. Once implemented and green, update
    `docs/architecture/research-assistant.md` (the baseline doc) to describe
    the new `investigate` + `finalize_report` pipeline, replacing the
    current single-loop description in §7.2 and extending the Evidence
    Ledger section in §9. Follow-up step, not part of this implementation
    PR.

    **Status:** still pending — not done as part of this implementation.
