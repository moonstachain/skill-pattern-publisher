# Skill Pattern Format

Use this schema when reducing a confirmed strategy into a reusable skill.

## Required Fields

- `skill_name`: lowercase slug, letters numbers and hyphens only
- `goal`: one sentence describing the skill outcome
- `trigger_description`: when Codex should use the skill
- `entry_commands`: one or more commands that represent the preferred execution path
- `constraints`: non-negotiable rules for the workflow
- `expected_outputs`: the concrete outputs the skill should produce

## Optional Fields

- `required_files`: extra files to scaffold under the generated skill
- `repo_visibility`: `public` or `private`
- `publish_status`: usually `READY_LOCAL`
- `display_name`
- `short_description`
- `default_prompt`

## Rules

- Prefer one clear path over multiple fallback paths.
- Keep trigger text broad enough to match real requests, but specific enough to avoid false positives.
- Do not include secrets, cookies, login state, or local machine credentials.
- Keep repositories one-skill-per-repo.
- Default to local generation first, then explicit publish later.
