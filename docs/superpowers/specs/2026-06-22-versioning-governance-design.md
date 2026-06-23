# Versioning Governance Design

**Status:** approved

**Date:** 2026-06-22

## Problem

Modi Harness has used release numbers for three different purposes:

- published package releases;
- product-development themes;
- design and implementation work items.

That made the `0.x` sequence look like a completion meter. Small pieces of work
were named as versions before a release boundary existed, and reaching `0.7.x`
created false pressure to approach `1.0`.

## Decision

Separate three namespaces:

1. **Package versions** describe released artifacts and compatibility.
2. **Milestones and work-item names** describe product intent and delivery.
3. **Protocol versions** describe persisted or externally consumed contracts.

The authoritative policy lives in `docs/project/versioning.md`. The
documentation index and development roadmap link to it, while audience-specific
guides summarize it rather than creating competing rules.

Existing tags, changelog entries, and historical documents remain unchanged.
The policy applies prospectively from the current `0.7.1` line.

## Release model

Modi Harness remains pre-1.0 for as long as its public contracts are still being
discovered. Minor numbers are not decimal fractions: `0.10.0` follows `0.9.x`,
and no `0.x` value creates an obligation to release `1.0`.

Patch releases preserve public contracts. Minor releases carry coherent,
user-visible capability changes and are the only pre-1.0 releases allowed to
introduce breaking public-contract changes. Work is bundled around a release
boundary; completing one task or design does not trigger a version bump.

`1.0.0` is gated by an explicit stability commitment, not feature completeness
or a calendar target. Its readiness criteria cover public API compatibility,
versioned persisted protocols, migration policy, governed-execution reliability,
security and operations documentation, and sustained real-world validation.

## Naming model

- Release names use SemVer/PEP 440 forms such as `0.8.0` and Git tags such as
  `v0.8.0`.
- Product milestones use capability names, optionally with a stable milestone
  identifier; they do not reserve a package version.
- Specs and plans use `YYYY-MM-DD-<capability>-design.md` and
  `YYYY-MM-DD-<capability>-plan.md`.
- Persisted protocols use independent identifiers such as `modi.trace/v1` and
  do not inherit the package version.

## Rollout

- Add the authoritative policy.
- Link it from the documentation index and development roadmap.
- Replace the plugin guide's local versioning rule with a concise compatibility
  summary and a link to the policy.
- Do not rename historical files or rewrite published tags.

This is a documentation-governance change. Verification consists of checking
links, terminology, version examples, and the Git diff; runtime tests are not
required.
