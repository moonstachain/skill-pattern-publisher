---
name: skill-pattern-publisher
description: Use when a user wants to formalize a repeated workflow as a skill, keep
  it local first, and publish one or more skills to GitHub later.
---

# Skill Pattern Publisher

Turn a confirmed strategy into a reusable Codex skill and publish selected skills to GitHub on demand.

## When To Use

Use when a user wants to formalize a repeated workflow as a skill, keep it local first, and publish one or more skills to GitHub later.

## Workflow

1. Reduce the agreed strategy into a single `Skill Pattern` file.
2. Use `scripts/skill_pattern_pipeline.py materialize-skill` to generate the local skill and register it.
3. Validate the generated skill before any publish step.
4. Publish only when the user explicitly asks for GitHub sync.

Read `references/skill-pattern-format.md` when you need the exact `Skill Pattern` fields.

## Commands

```bash
python3 scripts/skill_pattern_pipeline.py materialize-skill --pattern-file patterns/example.yaml --skills-root local-skills --registry skills-registry.json
```

```bash
python3 scripts/skill_pattern_pipeline.py publish-skills --all-ready --registry skills-registry.json
```

## Constraints

- Keep the workflow local-first and do not publish unless the user explicitly asks.
- Use one repository per skill and do not persist GitHub tokens in files or remotes.
- Keep generated skills on the strong template with SKILL.md, agents/openai.yaml, scripts/, and .gitignore.
- Use `--all-ready` only when the user explicitly wants batch publish behavior.

## Expected Outputs

- A generated local skill directory validated before registry insertion.
- A registry entry that tracks validation, publish status, and GitHub repo URL.
- A publish flow that creates or syncs per-skill repositories without storing the PAT.
