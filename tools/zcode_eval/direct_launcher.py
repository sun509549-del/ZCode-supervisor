"""Non-LLM direct launcher experiment runner for ZCode comparisons."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .codex_usage import non_negative_int, sha256_bytes
except ImportError:  # pragma: no cover - direct script execution
    from codex_usage import non_negative_int, sha256_bytes

RUNNER_VERSION = "0.1.0"
DEFAULT_ARTIFACT_ROOT = Path("artifacts/zcode-direct-launcher")
DEFAULT_ENV_ALLOWLIST = ("PATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE")
GIT_CONTROL_ENV_VARS = {
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
}
FORBIDDEN_ENV_RE = re.compile(r"(OPENAI|CODEX|ZAI|Z_AI|API.?KEY|TOKEN|SECRET|PASSWORD|AUTH|CREDENTIAL)", re.I)
SECRET_VALUE_PATTERNS = (
    re.compile(rb"(?i)(api[\s_-]?key|secret|token|password)\s*[:=]\s*['\"]?[^'\"\s]{12,}"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
)
SECRET_PATH_NEEDLES = (".env", "id_rsa", "id_ed25519", ".ssh", "credential", "credentials")
QUALITY_GATE_FIELDS = ("allowed_files_only", "scope_safety", "validation_result", "artifact_quality")
MAX_UNTRACKED_SCAN_BYTES = 1_000_000


def utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def safe_env(extra_names: list[str]) -> dict[str, str]:
    names = list(DEFAULT_ENV_ALLOWLIST)
    for name in extra_names:
        if name in GIT_CONTROL_ENV_VARS:
            raise ValueError(f"refusing to allow Git control env var: {name}")
        if FORBIDDEN_ENV_RE.search(name):
            raise ValueError(f"refusing to allow secret-like env var: {name}")
        names.append(name)
    return {
        name: os.environ[name]
        for name in dict.fromkeys(names)
        if name in os.environ and name not in GIT_CONTROL_ENV_VARS
    }


def scan_secret_bytes(data: bytes) -> bool:
    return any(pattern.search(data) for pattern in SECRET_VALUE_PATTERNS)


def redact_bytes(data: bytes) -> bytes:
    redacted = data
    for pattern in SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(b"<redacted>", redacted)
    return redacted


def parse_stdout_payload(stdout: bytes) -> dict[str, Any] | None:
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def quality_from_payload(payload: dict[str, Any] | None) -> dict[str, str]:
    summary = payload.get("run_result_summary") if isinstance(payload, dict) else None
    summary = summary if isinstance(summary, dict) else {}
    audit = payload.get("audit") if isinstance(payload, dict) else None
    audit = audit if isinstance(audit, dict) else {}
    validation = audit.get("validation") if isinstance(audit.get("validation"), dict) else {}
    scope = first_present(summary.get("scope_safety"), audit.get("scope_safety"))
    validation_result = first_present(summary.get("validation_result"), audit.get("validation_result"))
    if validation_result is None:
        validation_ok = first_present(summary.get("validation_ok"), validation.get("ok"))
        if validation_ok is True:
            validation_result = "pass"
        elif validation_ok is False:
            validation_result = "fail"
    artifact = first_present(summary.get("artifact_quality"), audit.get("artifact_quality"))
    repair = first_present(summary.get("codex_repair_size_recommendation"), audit.get("codex_repair_size_recommendation"))
    return {
        "allowed_files_only": "pass" if scope == "pass" else ("fail" if scope == "fail" else "unknown"),
        "scope_safety": scope or "unknown",
        "validation_result": validation_result or "unknown",
        "artifact_quality": artifact or "unknown",
        "codex_repair_size": repair or "unknown",
    }


def quality_gate_failed(quality: dict[str, str]) -> bool:
    for field in QUALITY_GATE_FIELDS:
        value = str(quality.get(field, "unknown")).lower()
        if value in ("", "unknown"):
            continue
        if value != "pass":
            return True
    return False


def git_lines(args: list[str], cwd: Path) -> list[str] | None:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def git_root(cwd: Path) -> Path | None:
    lines = git_lines(["rev-parse", "--show-toplevel"], cwd)
    if not lines:
        return None
    return Path(lines[0]).resolve()


def path_is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def git_ignores_path(path: Path, cwd: Path) -> bool:
    root = git_root(cwd)
    if root is None or not path_is_inside(path, root):
        return True
    rel = path.resolve().relative_to(root)
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(rel)],
            cwd=root,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def raw_artifact_paths_are_ignored(paths: list[Path], cwd: Path) -> bool:
    return all(git_ignores_path(path, cwd) for path in paths)


def parse_porcelain_path(line: str) -> str | None:
    if len(line) < 4:
        return None
    path = line[3:]
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[1]
    return path.strip() or None


def changed_files(cwd: Path) -> list[str] | None:
    status = git_lines(["status", "--porcelain", "--untracked-files=all"], cwd)
    if status is None:
        return None
    files = [path for line in status if (path := parse_porcelain_path(line))]
    return sorted(set(files))


def secret_like_path(path: str) -> bool:
    lowered = path.lower()
    return any(needle in lowered for needle in SECRET_PATH_NEEDLES)


def untracked_files(cwd: Path) -> list[str]:
    status = git_lines(["status", "--porcelain", "--untracked-files=all"], cwd) or []
    return sorted(
        path
        for line in status
        if line.startswith("?? ") and (path := parse_porcelain_path(line))
    )


def git_diff_artifacts(cwd: Path) -> dict[str, Any]:
    complete = True
    secret_path_found = False
    diff_parts: list[bytes] = []
    stat_parts: list[bytes] = []
    try:
        diff_parts.append(subprocess.run(["git", "diff"], cwd=cwd, capture_output=True, timeout=10, check=False).stdout)
        diff_parts.append(subprocess.run(["git", "diff", "--cached"], cwd=cwd, capture_output=True, timeout=10, check=False).stdout)
        stat_parts.append(subprocess.run(["git", "diff", "--stat"], cwd=cwd, capture_output=True, timeout=10, check=False).stdout)
        stat_parts.append(subprocess.run(["git", "diff", "--cached", "--stat"], cwd=cwd, capture_output=True, timeout=10, check=False).stdout)
    except (OSError, subprocess.TimeoutExpired):
        return {"diff": b"", "stat": b"", "complete": False, "secret_path_found": False}
    for rel in untracked_files(cwd):
        path = (cwd / rel).resolve()
        if not path_is_inside(path, cwd) or not path.is_file():
            complete = False
            continue
        stat_parts.append(f"?? {rel}\n".encode("utf-8"))
        if secret_like_path(rel):
            secret_path_found = True
            complete = False
            continue
        if path.stat().st_size > MAX_UNTRACKED_SCAN_BYTES:
            complete = False
            continue
        diff_parts.extend([
            f"\n--- untracked file: {rel}\n".encode("utf-8"),
            path.read_bytes(),
            b"\n",
        ])
    return {
        "diff": b"".join(diff_parts),
        "stat": b"".join(stat_parts),
        "complete": complete,
        "secret_path_found": secret_path_found,
    }


def write_log(path: Path, data: bytes, *, redact: bool) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(redact_bytes(data) if redact else data)
    return str(path)


def normalized_command(command: list[str]) -> list[str]:
    return command[1:] if command and command[0] == "--" else command


def command_fingerprint(command: list[str]) -> dict[str, Any]:
    command = normalized_command(command)
    encoded = json.dumps(command, separators=(",", ":")).encode("utf-8")
    return {
        "executable": Path(command[0]).name if command else None,
        "argv_count": len(command),
        "sha256": sha256_bytes(encoded),
    }


def build_manifest(
    args: argparse.Namespace,
    *,
    artifact_dir: Path,
    stdout: bytes,
    stderr: bytes,
    returncode: int | None,
    timed_out: bool,
    skipped_reason: str | None,
    env_names: list[str],
) -> dict[str, Any]:
    raw_stdout_path = artifact_dir / "stdout.raw.log"
    raw_stderr_path = artifact_dir / "stderr.raw.log"
    raw_diff_path = artifact_dir / "diff.raw.patch"
    raw_logs_gitignored = raw_artifact_paths_are_ignored(
        [raw_stdout_path, raw_stderr_path, raw_diff_path],
        args.workspace,
    )
    stdout_has_secret = scan_secret_bytes(stdout)
    stderr_has_secret = scan_secret_bytes(stderr)
    diff_info = git_diff_artifacts(args.workspace)
    diff = diff_info["diff"]
    diffstat = diff_info["stat"]
    diff_has_secret = scan_secret_bytes(diff) or bool(diff_info["secret_path_found"])
    secret_failed = stdout_has_secret or stderr_has_secret or diff_has_secret
    redact_logs = secret_failed or not raw_logs_gitignored
    suffix = ".redacted.log" if redact_logs else ".raw.log"
    stdout_path = write_log(artifact_dir / f"stdout{suffix}", stdout, redact=redact_logs)
    stderr_path = write_log(artifact_dir / f"stderr{suffix}", stderr, redact=redact_logs)
    diff_path = write_log(artifact_dir / ("diff.redacted.patch" if redact_logs else "diff.raw.patch"), diff, redact=redact_logs)
    files = changed_files(args.workspace)
    payload = parse_stdout_payload(stdout)
    quality = quality_from_payload(payload)
    if args.allowed and files is not None:
        allowed = set(args.allowed)
        quality["allowed_files_only"] = "pass" if set(files) <= allowed else "fail"
    quality_failed = quality_gate_failed(quality)
    budget_reasons: list[str] = []
    if timed_out:
        budget_reasons.append("wall_clock_timeout")
    if diff_info["complete"] is not True:
        budget_reasons.append("diff_scan_incomplete")
    if files is not None and len(files) > args.max_changed_files:
        budget_reasons.append("max_changed_files_exceeded")
    manifest = {
        "schema_version": "zcode_direct_launcher_manifest.v1",
        "runner_version": RUNNER_VERSION,
        "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "run_id": args.run_id or str(uuid.uuid4()),
        "mode": "zcode_direct_launcher",
        "dry_run": skipped_reason == "dry_run",
        "ok": (
            returncode == 0
            and not quality_failed
            and not secret_failed
            and raw_logs_gitignored
            and not budget_reasons
            and skipped_reason is None
        ),
        "skipped_reason": skipped_reason,
        "command": command_fingerprint(args.command or []),
        "command_exit_code": returncode,
        "timed_out": timed_out,
        "worktree_mode": args.worktree_mode,
        "env_policy": "allowlist",
        "env_names": env_names,
        "changed_files": files,
        "changed_file_count": len(files) if files is not None else None,
        "quality": quality,
        "safety": {
            "secret_scan_result": "fail" if secret_failed else "pass",
            "raw_logs_gitignored": raw_logs_gitignored,
            "untrusted_artifact_boundary": True,
            "budget_guard_hit": bool(budget_reasons),
            "budget_guard_reasons": budget_reasons,
        },
        "io_visibility": {
            "launcher_stdout_bytes_total": len(stdout),
            "launcher_stderr_bytes_total": len(stderr),
            "diff_bytes_total": len(diff),
            "diffstat_bytes_shown_to_codex": len(diffstat),
            "codex_visible_launcher_bytes": 0,
        },
        "artifacts": {
            "artifact_dir": str(artifact_dir),
            "launcher_stdout_log": stdout_path,
            "launcher_stderr_log": stderr_path,
            "diff_path": diff_path,
        },
    }
    visible = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    manifest["io_visibility"]["codex_visible_launcher_bytes"] = len(visible)
    if len(visible) > args.max_visible_bytes:
        manifest["safety"]["budget_guard_hit"] = True
        manifest["safety"]["budget_guard_reasons"].append("max_visible_bytes_exceeded")
        manifest["ok"] = False
    return manifest


def run_launcher(args: argparse.Namespace) -> dict[str, Any]:
    artifact_dir = args.artifact_dir or DEFAULT_ARTIFACT_ROOT / f"{utc_slug()}__direct-launcher"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        return build_manifest(
            args,
            artifact_dir=artifact_dir,
            stdout=b"",
            stderr=b"",
            returncode=None,
            timed_out=False,
            skipped_reason="dry_run",
            env_names=list(safe_env(args.allow_env).keys()),
        )
    args.command = normalized_command(args.command or [])
    if not args.command:
        return build_manifest(
            args,
            artifact_dir=artifact_dir,
            stdout=b"",
            stderr=b"command is required unless --dry-run is used\n",
            returncode=None,
            timed_out=False,
            skipped_reason="command_missing",
            env_names=list(safe_env(args.allow_env).keys()),
        )
    if args.worktree_mode not in {"disposable", "tmp_clone", "fixture"}:
        return build_manifest(
            args,
            artifact_dir=artifact_dir,
            stdout=b"",
            stderr=b"worktree_mode must be disposable, tmp_clone, or fixture\n",
            returncode=None,
            timed_out=False,
            skipped_reason="unsafe_worktree_mode",
            env_names=list(safe_env(args.allow_env).keys()),
        )
    env = safe_env(args.allow_env)
    try:
        result = subprocess.run(
            args.command,
            cwd=args.workspace,
            env=env,
            capture_output=True,
            timeout=args.max_wall_clock_sec,
            check=False,
        )
        return build_manifest(
            args,
            artifact_dir=artifact_dir,
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
            timed_out=False,
            skipped_reason=None,
            env_names=list(env.keys()),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, bytes) else b""
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
        return build_manifest(
            args,
            artifact_dir=artifact_dir,
            stdout=stdout,
            stderr=stderr,
            returncode=124,
            timed_out=True,
            skipped_reason=None,
            env_names=list(env.keys()),
        )
    except OSError as exc:
        return build_manifest(
            args,
            artifact_dir=artifact_dir,
            stdout=b"",
            stderr=str(exc).encode("utf-8", errors="replace"),
            returncode=127,
            timed_out=False,
            skipped_reason="command_os_error",
            env_names=list(env.keys()),
        )


def run_zcode_direct_launcher_command(args: argparse.Namespace) -> int:
    try:
        manifest = run_launcher(args)
    except ValueError as exc:
        manifest = {"ok": False, "error": str(exc), "env_policy": "allowlist"}
    manifest_path = args.manifest_out
    if manifest_path:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest["artifacts"] = {**manifest.get("artifacts", {}), "manifest_path": str(manifest_path)}
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest.get("ok") or manifest.get("dry_run") else 1


def add_direct_launcher_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "run-zcode-direct-launcher",
        help="Run a non-LLM ZCode experiment command with env allowlist and compact manifest output.",
    )
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--worktree-mode", choices=("disposable", "tmp_clone", "fixture", "current", "unknown"), default="unknown")
    parser.add_argument("--max-wall-clock-sec", type=non_negative_int, default=600)
    parser.add_argument("--max-visible-bytes", type=non_negative_int, default=6000)
    parser.add_argument("--max-changed-files", type=non_negative_int, default=10)
    parser.add_argument("--allowed", action="append", default=[])
    parser.add_argument("--allow-env", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    parser.set_defaults(func=run_zcode_direct_launcher_command)
