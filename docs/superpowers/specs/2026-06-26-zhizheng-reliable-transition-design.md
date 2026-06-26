# Zhizheng Reliable Transition Design

Date: 2026-06-26

## Problem

The Zhizheng web agent currently treats a successful browser click as if the business workflow advanced. In the observed failure, clicking `完成拍摄` returned `ok=true`, but a later observation showed the app at `/home`. The existing `browser_wait_for_text` also treats any matching text as success, even when the text may come from stale content, a homepage card, or historical messages.

This creates three risks:

- The agent records a successful flow step before the page has stabilized.
- The agent uses stale candidate IDs after the page has returned to `/home`.
- The agent narrates weak text matches as business success.

## Goal

Make Zhizheng automation reliable even when the WebAPP asynchronously returns to `/home`.

After every meaningful page-changing action, the runtime must distinguish:

- the DOM action happened,
- the page reached a reliable business state,
- the page returned to a recoverable homepage state,
- the state is unchanged or ambiguous,
- the action failed.

Only verified business transitions may be recorded as successful flow steps.

## Non-Goals

- Do not prevent the WebAPP from returning to `/home`; that may be legitimate application behavior.
- Do not encode a fixed Zhizheng button sequence.
- Do not make `browser_wait_for_text` a business-level assertion tool.
- Do not allow the model to recover by reusing stale candidates.

## Recommended Approach

Use a state transaction around page-changing tools.

Each action tool captures a pre-action state, performs the DOM operation, waits briefly for route and DOM stabilization, observes the post-action state, classifies the transition, and returns a structured contract that tells the agent what it may do next.

This makes the tool layer responsible for truth and leaves the model to choose among explicit next actions.

## Transition Contract

`browser_click_candidate` and `browser_click_candidate_and_upload` should return:

- `ok`: true only when the tool call itself completed without an internal exception.
- `click_ok`: true when the DOM click happened.
- `upload_ok`: present for upload tools when files were actually accepted by the page.
- `before_state`: compact page state before the action.
- `post_observe`: page observation after route and DOM stabilization.
- `transition_status`: one of:
  - `advanced`: page model shows a new reliable workflow state.
  - `returned_home`: page is exactly `/home` after the action.
  - `unchanged`: page state and available business actions did not materially change.
  - `ambiguous`: state changed, but not enough to prove business progress.
  - `failed`: click/upload failed or page reports an error.
- `recording_allowed`: true only for `advanced` or another explicitly safe success state.
- `must_not_record_flow_step`: true whenever `recording_allowed` is false.
- `recovery_action_cards`: present when `transition_status=returned_home`.
- `next_step`: a machine-readable instruction for the agent.

The legacy `recorded` action entry may remain for traceability, but it must not imply flow-step success.

## State Classification

The post-action classifier should use `zhizheng_page_model` as the primary source.

Classification rules:

- `advanced`: route is workflow-like and one of these is true:
  - progress advanced,
  - the latest assistant action set changed to the expected next business task,
  - an upload result is visible and accepted,
  - detail/workflow markers are stronger than the previous state.
- `returned_home`: route is exactly `/home` and page model is `homepage_or_case_list`.
- `unchanged`: route, progress, latest assistant actions, and business actions are effectively the same.
- `ambiguous`: visible text or URL changed, but the page model cannot prove progress.
- `failed`: the tool result is false, the page shows known error markers, or upload did not complete when upload was required.

Homepage return is not automatically a failure. It is a recovery-required state.

## Stabilization

After a page-changing action, the tool should wait for a stable page signature before returning.

The signature should include:

- URL/path,
- visible text digest,
- progress tuple if present,
- latest assistant action labels,
- business action labels,
- candidate count.

Stability means the same signature appears across at least two short polling intervals, or a bounded timeout expires. The timeout should be short enough for CLI ergonomics, around 1.5 to 3 seconds by default.

## Recovery from Homepage

When `transition_status=returned_home`, the runtime should:

1. Observe the current homepage.
2. Generate recovery action cards from the current candidates and target `record_id`.
3. Reject stale candidates from older observes.
4. Return `recording_allowed=false`.
5. Tell the agent to present recovery cards to the human and execute only a newly confirmed current candidate.

The agent must not say the previous business step succeeded unless the post-action classifier marked it recordable.

## Weak Text Matching

`browser_wait_for_text` should be demoted to a low-level convenience tool.

It should return:

- matched text,
- current URL/path,
- current `zhizheng_route_state`,
- a warning that the match is not business proof.

Agent and skill instructions should forbid using `browser_wait_for_text` as the only proof of a Zhizheng transition. Business proof must come from `browser_observe`, transition contract fields, or a dedicated assertion tool.

## Flow Recording

`browser_record_flow_step` should only accept the most recent successful transition when:

- `recording_allowed=true`,
- the recorded action ID or candidate ID matches the last transition,
- no newer observe invalidated the transition,
- the transition did not require recovery.

If these conditions are not met, return `ok=false` with:

- `reason`,
- `last_transition_status`,
- `must_not_record_flow_step=true`,
- `next_step`.

## Agent and Skill Updates

`agent.md` and `skills/zhizheng/SKILL.md` should instruct the model:

- Treat `click_ok=true` as only DOM execution, not business success.
- Only record flow steps when `recording_allowed=true`.
- If `transition_status=returned_home`, present recovery cards and ask for confirmation.
- Never reuse stale candidate IDs.
- Never use `browser_wait_for_text` alone as business proof.
- Report `ambiguous`, `unchanged`, and `failed` as real states instead of narrating them as success.

## Tests

Add focused tests in `tests/agents/test_modi_webagent.py` for the nested agent runtime:

- Click returns `returned_home` when post-observe route is `/home`.
- `returned_home` produces recovery action cards and disables flow recording.
- `advanced` allows flow recording.
- `unchanged` disables flow recording.
- `browser_wait_for_text` returns route metadata and a weak-proof warning.
- `browser_record_flow_step` rejects an action whose last transition has `recording_allowed=false`.
- Stale homepage candidate IDs remain rejected after a returned-home transition.

Existing harness tests should remain green.

## Rollout

Implement this inside `agents/modi-webagent` first. Then update outer harness tests that validate the agent prompt, skill text, and tool behavior.

Because `agents/modi-webagent` is a nested Git repository, commit order should be:

1. Commit runtime and skill changes in `agents/modi-webagent`.
2. Commit the updated gitlink and outer tests in `modi-harness`.

## Success Criteria

The same `完成拍摄` scenario should no longer produce a misleading success narrative. If the app returns to `/home`, the tool returns `transition_status=returned_home`, refuses flow recording, and gives the agent fresh recovery cards. The agent then asks for confirmation before re-entering the case.
