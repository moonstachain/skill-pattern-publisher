# Skill Pattern Publisher

## What it is
Skill Pattern Publisher is a Codex skill for turning a confirmed workflow into a reusable local skill, validating the generated structure, and publishing selected skills to GitHub on demand.

## Who it's for
This repo is for operators who want a local-first pipeline for converting repeated workflows into maintainable Codex skills and then syncing those skills to GitHub only when publication is explicitly requested.

## Quick start
```bash
python3 scripts/skill_pattern_pipeline.py materialize-skill --pattern-file patterns/example.yaml --skills-root local-skills --registry skills-registry.json
```

## Inputs
- A YAML or JSON skill pattern file describing the target skill.
- A destination `skills-root` for generated local skills.
- A `skills-registry.json` path for publish metadata.
- A GitHub token only when publication is requested.

## Outputs
- A generated local skill directory with `SKILL.md`, `agents/openai.yaml`, `scripts/`, and `.gitignore`.
- A validated registry entry tracking materialization and publish status.
- Optional GitHub repository creation and publish results when running the publish flow.

## Constraints
- The workflow is local-first and should not publish automatically.
- GitHub tokens must not be persisted into repo files or remotes.
- Use one repository per skill.
- Batch publishing with `--all-ready` should be used only when the user explicitly wants it.

## Example
Capture a repeated Codex workflow in a pattern file, materialize it into `local-skills/<skill-name>`, validate the generated structure, and later publish that skill to a dedicated GitHub repository after review.

## Project structure
- `scripts/`: materialization and publish pipeline.
- `patterns/`: example pattern inputs.
- `references/`: exact skill-pattern field definitions.
- `agents/`: Codex interface metadata.

