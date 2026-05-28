# Agent Loader

Agent Loader turns a Markdown agent file into an `AgentProfile`.

## Input

- Agent name or file path.
- Markdown file with frontmatter and instruction body.
- Runtime config and task overrides.

## Output

```python
class AgentProfile(TypedDict):
    name: str
    description: str
    instruction: str
    default_tools: list[str]
    default_skills: list[str]
    output_contract: dict | None
    permission_profile: dict | None
    safety_constraints: list[str]
    metadata: dict
```

## Rules

- Required frontmatter: `name`, `description`.
- Optional frontmatter: `tools`, `skills`, `output_contract`, `permission_profile`, `safety_constraints`.
- Markdown body is the agent instruction.
- Agent instructions can constrain behavior but cannot grant permission.
- Policy Gate remains the authority for side effects, approval, denial, and review.
- Loader only parses and normalizes; it does not select skills, execute tools, or call models.
