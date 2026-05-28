# Context Manager

## Module

`modi_harness.context`

## Purpose

Build deterministic `ContextPack` objects for model calls.

## Design

Implement:

- `ContextManager`
- `build_context(state, agent, skills, workspace_index, tools, output_contract) -> ContextPack`
- message windowing
- workspace index formatting
- trust annotation builder
- stable context hash

## Trust Model

Trusted:

- system instruction
- agent instruction
- active skill instruction, below agent instruction

Untrusted:

- tool results
- user documents
- workspace files
- references
- examples

## LangChain Integration

Context Manager should be able to emit:

- Modi `ContextPack`
- LangChain message list
- tool description payloads suitable for LangChain binding

## Rules

- Preserve instruction hierarchy.
- Do not inline large files.
- Include references by path, id, summary, or metadata.
- Expose only tools visible under agent, skill, runtime, and policy constraints.
- Keep output deterministic for trace hashing.
- Keep the canonical context as Modi `ContextPack`; convert to LangChain messages at the boundary.

## Tests

- deterministic context hash
- untrusted annotations
- message windowing
- tool visibility filtering
- large file reference behavior
