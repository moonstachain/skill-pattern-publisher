#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib import error, request

import yaml


REGISTRY_VERSION = 1
DEFAULT_REGISTRY_PATH = Path("skills-registry.json")
DEFAULT_SKILLS_ROOT = Path("local-skills")
DEFAULT_GITHUB_API = "https://api.github.com"
MAX_SKILL_NAME_LENGTH = 64
BASE_DIRS = ("agents", "scripts")
BASE_FILES = ("SKILL.md", "agents/openai.yaml", ".gitignore")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_skill_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise ValueError("skill_name cannot be empty after normalization")
    if len(normalized) > MAX_SKILL_NAME_LENGTH:
        raise ValueError(f"skill_name is too long ({len(normalized)} > {MAX_SKILL_NAME_LENGTH})")
    return normalized


def title_case(skill_name: str) -> str:
    return " ".join(part.capitalize() for part in skill_name.split("-"))


def ensure_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field_name} must be a non-empty list of strings")
    return [item.strip() for item in value]


def repo_name_for_skill(skill_name: str) -> str:
    return skill_name


def short_description_for(goal: str, trigger_description: str) -> str:
    base = goal.strip() or trigger_description.strip() or "Reusable automation skill"
    base = re.sub(r"\s+", " ", base)
    if len(base) < 25:
        base = f"{base} automation skill"
    if len(base) > 64:
        base = base[:61].rstrip() + "..."
    return base


def default_prompt_for(skill_name: str, goal: str) -> str:
    base = re.sub(r"\s+", " ", goal.strip())
    if not base:
        base = "execute the documented workflow"
    return f"Use ${skill_name} to {base.rstrip('.')}."


def yaml_safe_dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip() + "\n"


@dataclass
class SkillPattern:
    skill_name: str
    goal: str
    trigger_description: str
    entry_commands: list[str]
    constraints: list[str]
    expected_outputs: list[str]
    required_files: list[str]
    repo_visibility: str = "public"
    publish_status: str = "READY_LOCAL"
    display_name: Optional[str] = None
    short_description: Optional[str] = None
    default_prompt: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SkillPattern":
        skill_name = normalize_skill_name(str(payload.get("skill_name", "")))
        repo_visibility = str(payload.get("repo_visibility", "public")).strip().lower()
        if repo_visibility not in {"public", "private"}:
            raise ValueError("repo_visibility must be 'public' or 'private'")

        publish_status = str(payload.get("publish_status", "READY_LOCAL")).strip().upper()
        if publish_status not in {"READY_LOCAL", "PUBLISHED"}:
            raise ValueError("publish_status must be READY_LOCAL or PUBLISHED")

        spec = cls(
            skill_name=skill_name,
            goal=str(payload.get("goal", "")).strip(),
            trigger_description=str(payload.get("trigger_description", "")).strip(),
            entry_commands=ensure_list(payload.get("entry_commands"), "entry_commands"),
            constraints=ensure_list(payload.get("constraints"), "constraints"),
            expected_outputs=ensure_list(payload.get("expected_outputs"), "expected_outputs"),
            required_files=[str(item).strip() for item in payload.get("required_files", []) if str(item).strip()],
            repo_visibility=repo_visibility,
            publish_status=publish_status,
            display_name=(str(payload.get("display_name")).strip() if payload.get("display_name") else None),
            short_description=(str(payload.get("short_description")).strip() if payload.get("short_description") else None),
            default_prompt=(str(payload.get("default_prompt")).strip() if payload.get("default_prompt") else None),
        )
        if not spec.goal:
            raise ValueError("goal is required")
        if not spec.trigger_description:
            raise ValueError("trigger_description is required")
        return spec


@dataclass
class RegistryEntry:
    skill_name: str
    local_path: str
    repo_name: str
    repo_visibility: str
    publish_status: str
    validation_passed: bool
    validated_at: Optional[str]
    github_repo_url: Optional[str]
    last_commit: Optional[str]
    last_materialized_at: str
    last_published_at: Optional[str]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RegistryEntry":
        return cls(**payload)


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, RegistryEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for item in raw.get("skills", []):
            entry = RegistryEntry.from_dict(item)
            self.entries[entry.skill_name] = entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": REGISTRY_VERSION,
            "updated_at": now_iso(),
            "skills": [asdict(entry) for entry in sorted(self.entries.values(), key=lambda item: item.skill_name)],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def upsert(self, entry: RegistryEntry) -> None:
        self.entries[entry.skill_name] = entry
        self.save()

    def get(self, skill_name: str) -> RegistryEntry:
        return self.entries[skill_name]

    def select(self, skill_names: Optional[Iterable[str]] = None, *, all_ready: bool = False, all_skills: bool = False) -> list[RegistryEntry]:
        if skill_names:
            selected = []
            for name in skill_names:
                normalized = normalize_skill_name(name)
                if normalized not in self.entries:
                    raise KeyError(f"Unknown skill in registry: {normalized}")
                selected.append(self.entries[normalized])
            return selected
        if all_skills:
            return sorted(self.entries.values(), key=lambda entry: entry.skill_name)
        if all_ready:
            return sorted(
                [entry for entry in self.entries.values() if entry.publish_status == "READY_LOCAL"],
                key=lambda entry: entry.skill_name,
            )
        raise ValueError("Select target skills with --skills, --all-ready, or --all")


def skill_frontmatter(pattern: SkillPattern) -> dict[str, str]:
    return {
        "name": pattern.skill_name,
        "description": pattern.trigger_description,
    }


def render_skill_md(pattern: SkillPattern) -> str:
    frontmatter = yaml_safe_dump(skill_frontmatter(pattern)).strip()
    body_lines = [
        "---",
        frontmatter,
        "---",
        "",
        f"# {title_case(pattern.skill_name)}",
        "",
        pattern.goal,
        "",
        "## Trigger",
        "",
        pattern.trigger_description,
        "",
        "## Entry Commands",
        "",
    ]
    for command in pattern.entry_commands:
        body_lines.extend(["```bash", command, "```", ""])

    body_lines.extend(["## Constraints", ""])
    for item in pattern.constraints:
        body_lines.append(f"- {item}")
    body_lines.append("")

    body_lines.extend(["## Expected Outputs", ""])
    for item in pattern.expected_outputs:
        body_lines.append(f"- {item}")
    body_lines.append("")

    body_lines.extend(["## Notes", "", "- Keep this skill self-contained and deterministic.", "- Update this file when trigger conditions or outputs change.", ""])
    return "\n".join(body_lines)


def render_openai_yaml(pattern: SkillPattern) -> str:
    display_name = pattern.display_name or title_case(pattern.skill_name)
    short_description = pattern.short_description or short_description_for(pattern.goal, pattern.trigger_description)
    default_prompt = pattern.default_prompt or default_prompt_for(pattern.skill_name, pattern.goal)
    payload = {
        "interface": {
            "display_name": display_name,
            "short_description": short_description,
            "default_prompt": default_prompt,
        }
    }
    return yaml_safe_dump(payload)


def render_gitignore() -> str:
    return "__pycache__/\n*.pyc\nstate/\nartifacts/\n.tmp/\n"


def placeholder_script_for(pattern: SkillPattern, script_name: str) -> str:
    title = title_case(pattern.skill_name)
    return (
        "#!/usr/bin/env python3\n"
        '"""\n'
        f"{title} placeholder script.\n"
        '"""\n\n'
        "def main() -> None:\n"
        f"    print({pattern.goal!r})\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )


def placeholder_markdown_for(relative_path: str, pattern: SkillPattern) -> str:
    return f"# {relative_path}\n\nPlaceholder file for {pattern.skill_name}.\n"


def ensure_required_files(skill_dir: Path, pattern: SkillPattern) -> None:
    generated_script = skill_dir / "scripts" / f"{pattern.skill_name.replace('-', '_')}.py"
    if not generated_script.exists():
        generated_script.write_text(placeholder_script_for(pattern, generated_script.name), encoding="utf-8")
        generated_script.chmod(0o755)

    for relative_path in pattern.required_files:
        relative = Path(relative_path)
        target = skill_dir / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".py":
            target.write_text(placeholder_script_for(pattern, target.name), encoding="utf-8")
            target.chmod(0o755)
        elif target.suffix in {".md", ".txt", ".yaml", ".yml", ".json"}:
            target.write_text(placeholder_markdown_for(relative_path, pattern), encoding="utf-8")
        else:
            target.write_text("", encoding="utf-8")


def validate_skill(skill_dir: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        errors.append("SKILL.md not found")
    else:
        content = skill_md.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            errors.append("SKILL.md frontmatter is missing or invalid")
        else:
            try:
                frontmatter = yaml.safe_load(match.group(1))
            except yaml.YAMLError as exc:
                errors.append(f"SKILL.md frontmatter YAML invalid: {exc}")
            else:
                if not isinstance(frontmatter, dict):
                    errors.append("SKILL.md frontmatter must be a mapping")
                else:
                    for key in ("name", "description"):
                        if not frontmatter.get(key):
                            errors.append(f"SKILL.md frontmatter missing {key}")

    openai_yaml = skill_dir / "agents" / "openai.yaml"
    if not openai_yaml.exists():
        errors.append("agents/openai.yaml not found")
    else:
        try:
            payload = yaml.safe_load(openai_yaml.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"agents/openai.yaml invalid YAML: {exc}")
        else:
            interface = payload.get("interface") if isinstance(payload, dict) else None
            if not isinstance(interface, dict):
                errors.append("agents/openai.yaml missing interface block")
            else:
                for key in ("display_name", "short_description", "default_prompt"):
                    if not interface.get(key):
                        errors.append(f"agents/openai.yaml missing interface.{key}")

    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.exists():
        errors.append("scripts directory not found")
    else:
        for script_path in scripts_dir.glob("*.py"):
            try:
                source = script_path.read_text(encoding="utf-8")
                compile(source, str(script_path), "exec")
            except SyntaxError as exc:
                message = exc.msg or "invalid syntax"
                errors.append(
                    f"Python syntax error in {script_path.name}:{exc.lineno}:{exc.offset}: {message}"
                )

    return not errors, errors


def write_skill(skill_dir: Path, pattern: SkillPattern) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    for directory in BASE_DIRS:
        (skill_dir / directory).mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(render_skill_md(pattern), encoding="utf-8")
    (skill_dir / "agents" / "openai.yaml").write_text(render_openai_yaml(pattern), encoding="utf-8")
    (skill_dir / ".gitignore").write_text(render_gitignore(), encoding="utf-8")
    ensure_required_files(skill_dir, pattern)


def materialize_skill(pattern_path: Path, registry_path: Path, skills_root: Path, force: bool = False) -> RegistryEntry:
    raw = yaml.safe_load(pattern_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("pattern file must contain a YAML/JSON object")
    pattern = SkillPattern.from_dict(raw)

    registry = Registry(registry_path)
    skill_dir = skills_root / pattern.skill_name
    if skill_dir.exists() and not force and pattern.skill_name not in registry.entries:
        raise FileExistsError(f"Skill directory already exists and is not registered: {skill_dir}")

    write_skill(skill_dir, pattern)
    valid, errors = validate_skill(skill_dir)
    if not valid:
        raise RuntimeError("Validation failed: " + "; ".join(errors))

    last_commit = git_last_commit(skill_dir)
    entry = RegistryEntry(
        skill_name=pattern.skill_name,
        local_path=str(skill_dir.resolve()),
        repo_name=repo_name_for_skill(pattern.skill_name),
        repo_visibility=pattern.repo_visibility,
        publish_status=pattern.publish_status,
        validation_passed=True,
        validated_at=now_iso(),
        github_repo_url=(registry.entries.get(pattern.skill_name).github_repo_url if pattern.skill_name in registry.entries else None),
        last_commit=last_commit,
        last_materialized_at=now_iso(),
        last_published_at=(registry.entries.get(pattern.skill_name).last_published_at if pattern.skill_name in registry.entries else None),
    )
    registry.upsert(entry)
    return entry


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True, check=False)


def git_repo_exists(skill_dir: Path) -> bool:
    return (skill_dir / ".git").exists()


def ensure_git_repo(skill_dir: Path) -> None:
    if git_repo_exists(skill_dir):
        return
    result = run_git(["init"], cwd=skill_dir)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    run_git(["branch", "-M", "main"], cwd=skill_dir)


def git_last_commit(skill_dir: Path) -> Optional[str]:
    if not git_repo_exists(skill_dir):
        return None
    result = run_git(["rev-parse", "HEAD"], cwd=skill_dir)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def ensure_git_identity(skill_dir: Path) -> None:
    name = run_git(["config", "user.name"], cwd=skill_dir).stdout.strip()
    email = run_git(["config", "user.email"], cwd=skill_dir).stdout.strip()
    if not name:
        run_git(["config", "user.name", "skill-pipeline"], cwd=skill_dir)
    if not email:
        run_git(["config", "user.email", "skill-pipeline@local.invalid"], cwd=skill_dir)


def git_is_dirty(skill_dir: Path) -> bool:
    result = run_git(["status", "--porcelain"], cwd=skill_dir)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return bool(result.stdout.strip())


def git_stage_all(skill_dir: Path) -> None:
    result = run_git(["add", "."], cwd=skill_dir)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def git_commit(skill_dir: Path, message: str) -> Optional[str]:
    if not git_is_dirty(skill_dir):
        return git_last_commit(skill_dir)
    result = run_git(["commit", "-m", message], cwd=skill_dir)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return git_last_commit(skill_dir)


def set_remote(skill_dir: Path, url: str) -> None:
    remotes = run_git(["remote"], cwd=skill_dir)
    remote_names = set(remotes.stdout.split())
    if "origin" in remote_names:
        result = run_git(["remote", "set-url", "origin", url], cwd=skill_dir)
    else:
        result = run_git(["remote", "add", "origin", url], cwd=skill_dir)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def push_with_token(skill_dir: Path, token: str) -> None:
    auth = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    result = run_git(
        [
            "-c",
            f"http.https://github.com/.extraheader=AUTHORIZATION: basic {auth}",
            "push",
            "-u",
            "origin",
            "main",
        ],
        cwd=skill_dir,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


class GitHubClient:
    def __init__(self, token: str, api_base: str = DEFAULT_GITHUB_API) -> None:
        self.token = token
        self.api_base = api_base.rstrip("/")

    def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = request.Request(
            f"{self.api_base}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "skill-pipeline",
            },
        )
        try:
            with request.urlopen(req) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8")
            raise RuntimeError(f"GitHub API {method} {path} failed: {exc.code} {detail}") from exc
        return json.loads(raw) if raw else {}

    def get_viewer_login(self) -> str:
        return str(self._request("GET", "/user")["login"])

    def repo_exists(self, owner: str, repo_name: str) -> Optional[dict[str, Any]]:
        req = request.Request(
            f"{self.api_base}/repos/{owner}/{repo_name}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "skill-pipeline",
            },
        )
        try:
            with request.urlopen(req) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            if exc.code == 404:
                return None
            detail = exc.read().decode("utf-8")
            raise RuntimeError(f"GitHub API GET /repos/{owner}/{repo_name} failed: {exc.code} {detail}") from exc

    def ensure_repo(self, owner: str, repo_name: str, visibility: str) -> dict[str, Any]:
        existing = self.repo_exists(owner, repo_name)
        if existing:
            return existing
        return self._request(
            "POST",
            "/user/repos",
            {
                "name": repo_name,
                "private": visibility == "private",
            },
        )


def classify_publish_action(entry: RegistryEntry, skill_dir: Path) -> str:
    if not entry.github_repo_url:
        return "publish_new"
    if not git_repo_exists(skill_dir):
        return "sync_existing"
    return "sync_existing" if git_is_dirty(skill_dir) else "skip_unchanged"


def publish_entries(
    registry_path: Path,
    skill_names: Optional[list[str]],
    *,
    all_ready: bool,
    all_skills: bool,
    token: str,
    owner: Optional[str] = None,
    commit_message: Optional[str] = None,
) -> list[dict[str, str]]:
    registry = Registry(registry_path)
    selected = registry.select(skill_names, all_ready=all_ready, all_skills=all_skills)
    client = GitHubClient(token)
    owner = owner or client.get_viewer_login()
    outcomes: list[dict[str, str]] = []

    for entry in selected:
        skill_dir = Path(entry.local_path)
        valid, errors = validate_skill(skill_dir)
        if not valid:
            raise RuntimeError(f"{entry.skill_name} validation failed before publish: {'; '.join(errors)}")

        ensure_git_repo(skill_dir)
        ensure_git_identity(skill_dir)
        git_stage_all(skill_dir)

        action = classify_publish_action(entry, skill_dir)
        repo = client.ensure_repo(owner, entry.repo_name, entry.repo_visibility)
        set_remote(skill_dir, repo["clone_url"])

        if action == "skip_unchanged":
            entry.github_repo_url = repo["html_url"]
            entry.publish_status = "PUBLISHED"
            entry.last_published_at = entry.last_published_at or now_iso()
            registry.upsert(entry)
            outcomes.append({"skill_name": entry.skill_name, "action": action, "repo_url": repo["html_url"]})
            continue

        default_message = "Initial commit" if not git_last_commit(skill_dir) else "Update skill content"
        committed = git_commit(skill_dir, commit_message or default_message)
        if committed is None:
            committed = git_last_commit(skill_dir)

        push_with_token(skill_dir, token)
        entry.github_repo_url = repo["html_url"]
        entry.publish_status = "PUBLISHED"
        entry.last_commit = committed
        entry.last_published_at = now_iso()
        entry.validation_passed = True
        entry.validated_at = now_iso()
        registry.upsert(entry)
        outcomes.append({"skill_name": entry.skill_name, "action": action, "repo_url": repo["html_url"]})

    return outcomes


def parse_skill_names(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def resolve_token(args: argparse.Namespace) -> str:
    if args.github_token:
        return args.github_token
    if args.github_token_env:
        token = os.environ.get(args.github_token_env)
        if token:
            return token
    raise RuntimeError("Provide a GitHub token with --github-token or via --github-token-env")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Materialize skills locally and publish them to GitHub.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    materialize = subparsers.add_parser("materialize-skill", help="Generate a local skill from a Skill Pattern file.")
    materialize.add_argument("--pattern-file", "--spec-file", dest="pattern_file", required=True, help="Path to YAML or JSON Skill Pattern.")
    materialize.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH), help="Path to skills-registry.json.")
    materialize.add_argument("--skills-root", default=str(DEFAULT_SKILLS_ROOT), help="Directory for generated local skills.")
    materialize.add_argument("--force", action="store_true", help="Overwrite an existing registered skill directory.")

    publish = subparsers.add_parser("publish-skills", help="Publish local skills from the registry to GitHub.")
    publish.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH), help="Path to skills-registry.json.")
    publish.add_argument("--skills", help="Comma-separated skill names to publish.")
    publish.add_argument("--all-ready", action="store_true", help="Publish all skills with READY_LOCAL status.")
    publish.add_argument("--all", action="store_true", help="Publish or sync all skills from the registry.")
    publish.add_argument("--github-token", help="GitHub PAT with repo scope. Prefer env for safety.")
    publish.add_argument("--github-token-env", default="GITHUB_TOKEN", help="Environment variable name that holds the PAT.")
    publish.add_argument("--github-owner", help="GitHub owner/login. Defaults to the token owner.")
    publish.add_argument("--commit-message", help="Override the generated commit message for this publish run.")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "materialize-skill":
        entry = materialize_skill(
            pattern_path=Path(args.pattern_file).expanduser().resolve(),
            registry_path=Path(args.registry).expanduser().resolve(),
            skills_root=Path(args.skills_root).expanduser().resolve(),
            force=args.force,
        )
        print(json.dumps(asdict(entry), ensure_ascii=False, indent=2))
        return 0

    if args.command == "publish-skills":
        token = resolve_token(args)
        outcomes = publish_entries(
            registry_path=Path(args.registry).expanduser().resolve(),
            skill_names=parse_skill_names(args.skills),
            all_ready=args.all_ready,
            all_skills=args.all,
            token=token,
            owner=args.github_owner,
            commit_message=args.commit_message,
        )
        print(json.dumps(outcomes, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
