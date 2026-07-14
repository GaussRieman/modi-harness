# Single-Node Public Web Research Implementation Plan

1. Replace the four-Node Research Workflow with one autonomous `research`
   Node and its final completion contract.
2. Replace the old Research Assistant tool set with one bounded
   `public_web_research` Operation using multiple public provider records,
   relevance ranking, deduplication, and compact fetching.
3. Replace the two old Skills with one `web-research` Skill describing the
   single-Node evidence and negative-result rules.
4. Collapse completion validation into one source-bound final briefing
   validator with precise repair feedback.
5. Delete digest/judge code, obsolete documentation, exports, and tests.
6. Add provider parsing, relevance, bounded fetching, Workflow, validator,
   trace, and CLI regression tests.
7. Run the full test suite, Ruff, mypy, and `git diff --check`; review and
   commit the implementation.
