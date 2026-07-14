# Agent and Skill

`ModiAgent` is an immutable declaration with at least one explicit Workflow.
It may be constructed in Python, loaded from a canonical package, or returned
by a trusted exact factory manifest. Markdown Agent declarations are not a
supported format.

A Skill is reusable professional method guidance. A Tool is an executable,
schema-governed Operation. Neither changes Workflow control.

Discovery returns `ModiAgent` values with source provenance; it never creates a
Harness or Session. Session construction rejects conflicting Agent names and
merges Agent Tools with kernel builtins.
