# Expected Behavior — support-bot-default

A successful run on this scenario should:

1. `account_lookup(account_id="acct_123")` (L1, allowed).
2. `knowledge_search("double charge billing dispute")` (L1, allowed).
3. Recognize this is a billing dispute → `escalation-policy` skill says escalate.
4. Propose `escalate_to_human` (L4 + `review_required` per the agent's permission profile) → run interrupts with `pending_approval`.
5. After `approve_action`, return a free-form reply to the customer that:
   - mirrors the situation in one short sentence
   - confirms what was looked up
   - states a human is now taking over
   - does not promise a refund timeline

## Trace Should Include

- `run_start`
- `memory_selection` showing `user` + `conversation` records loaded for `thread_demo_001`
- `policy_decision` for each tool call
- `approval_request` and `approval_granted` for `escalate_to_human`
- `output_validation` with status `final` (free-form pass-through)
- `run_end` with `status: completed`

## Edge Cases

- Run without `thread_id` → no `conversation` memory loaded; rest of flow unchanged.
- If the model proposes `escalate_to_human` a second time after rejection → `denied-retry` blocks at Tool Gateway before reaching Policy Gate.
- If the model writes a `feedback` memory based on tool result content alone → Policy Gate denies the `memory_write` (untrusted source without user round-trip).
