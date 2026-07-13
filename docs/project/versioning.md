# Versioning and Naming Policy

This document is the authoritative policy for Modi Harness package versions,
release names, product milestones, work-item names, and protocol versions. It
applies prospectively from the `0.7.1` release line. Historical tags, changelog
entries, specifications, and plans keep their original names.

## Principles

1. **A package version describes a released artifact and its compatibility.**
   It is not a progress percentage, roadmap position, or task identifier.
2. **A milestone describes product intent.** It may span several package
   releases and does not reserve a version number.
3. **A protocol version describes a durable contract.** It evolves separately
   from the Python package.
4. **`1.0.0` is a stability commitment, not a claim of completeness.** No
   feature set is ever completely finished.

## Package versions before 1.0

Modi Harness follows Semantic Versioning for release numbers and PEP 440 for
Python pre-release notation. While the package is in `0.y.z`:

- **Patch (`0.y.Z`)**: backwards-compatible fixes, documentation, performance
  improvements, and internal refactoring. Public contracts and persisted data
  formats must remain compatible within the minor line.
- **Minor (`0.Y.0`)**: a coherent set of user-visible capabilities. A minor
  release may change a public contract before `1.0`, but every such change must
  be intentional, documented, and accompanied by migration guidance.
- **Pre-release (`0.y.0aN`, `0.y.0bN`, `0.y.0rcN`)**: an artifact intended for
  focused validation before the corresponding final release.

A completed task, merged pull request, design document, or internal development
theme does not by itself require a version bump. Maintainers first define a
coherent release boundary, then assign its version.

Version components are integers, not decimal fractions. `0.10.0` follows
`0.9.x`; `0.20.0` and later pre-1.0 versions are valid. Reaching `0.9.x` never
creates an obligation to publish `1.0.0`.

## The 1.0 stability gate

There is no scheduled `1.0.0` release. It becomes eligible only when the
maintainers are willing to make all of these commitments:

- the supported public Python API is explicitly documented and has a defined
  compatibility and deprecation policy;
- persisted and externally consumed contracts, including Agent manifests,
  Policy decisions, Trace events, and Approval records, have independent
  schema or protocol versions;
- breaking changes have migration documentation and a stated support window;
- interrupt, approval, resume, replay, and audit behavior is reliable across
  supported deployment modes;
- trust boundaries, threat assumptions, operational requirements, and failure
  behavior are documented;
- release and upgrade procedures are repeatable;
- the core contracts have sustained real-world use beyond repository tests and
  demonstrations.

Meeting these criteria does not mean the product is finished. It means other
systems can depend on its core contracts without accepting uncontrolled change.

## Naming conventions

### Package releases

Use the bare version in package metadata and the `v`-prefixed form for Git tags:

```text
package: 0.8.0
tag:     v0.8.0
```

Do not use lettered pseudo-versions such as `v0.8a` for work streams. PEP 440
pre-release identifiers are reserved for artifacts that are actually released.

### Product milestones

Name milestones after the capability or outcome they deliver, for example:

```text
Auditable Action Lifecycle
Human-Centered Runtime
Token and Cost Attribution
```

A stable internal identifier such as `M-AUDIT-1` may be added when cross-document
tracking needs one. Do not put an unassigned package version in the milestone
name.

### Specifications and plans

Use dates and capability slugs:

```text
YYYY-MM-DD-<capability>-design.md
YYYY-MM-DD-<capability>-plan.md
```

The target release may be added later inside the document after the release
scope is accepted. It must not be used as the primary work-item identity.

### Protocols and persisted schemas

Contracts that may outlive a single process or package release receive their
own version identifiers, for example:

```text
modi.agent-manifest/v1
modi.policy-decision/v1
modi.trace/v1
modi.approval/v1
```

Changing the package version does not automatically change these identifiers.
A protocol version changes only when its compatibility contract changes.

## Release decision checklist

Before assigning or publishing a release:

1. Identify the user-visible release scope; do not derive it from a single task.
2. Review public APIs, CLI behavior, configuration, plugin contracts, and
   persisted formats for compatibility changes.
3. Choose patch, minor, or pre-release according to this policy.
4. Update `CHANGELOG.md` with user-visible changes and migration notes.
5. Keep the versions in `pyproject.toml` and `modi_harness.__version__` equal.
6. Run the release's required tests and documentation/link checks.
7. Create a Git tag only for an artifact that is ready to be published.

If the correct version class is unclear, delay assigning the number. A release
number should summarize an agreed boundary, not create one.

## Source-of-truth hierarchy

- This document owns versioning and naming policy.
- The latest approved file under `docs/superpowers/plans/` owns the current
  implementation boundary.
- `CHANGELOG.md` records shipped release changes.
- Architecture and reference documentation own runtime and contract behavior.

Audience-specific guides may summarize these rules, but must link here and must
not define an alternative version policy.
