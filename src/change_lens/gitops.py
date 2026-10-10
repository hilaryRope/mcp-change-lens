"""Read-only, hardened Git access.

Repository content is untrusted. Everything here uses argv lists (never a shell),
validates refs, pins the repo inside allowed roots, and disables git features that
can execute configured programs (external diff, textconv, fsmonitor, hooks).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REF_RE = re.compile(r"^[A-Za-z0-9._/~^@{}+-]{1,200}$")
MAX_BLOB_BYTES = 200_000
TIMEOUT = 30


class ChangeLensError(Exception):
    pass


def allowed_roots() -> list[Path]:
    raw = os.environ.get("CHANGE_LENS_ALLOWED_ROOTS")
    roots = raw.split(os.pathsep) if raw else [os.getcwd()]
    return [Path(r).expanduser().resolve() for r in roots if r]


def resolve_repo(repo_path: str) -> Path:
    p = Path(repo_path).expanduser().resolve()
    if not any(p == r or r in p.parents for r in allowed_roots()):
        raise ChangeLensError(
            f"{p} is outside the allowed roots. Set CHANGE_LENS_ALLOWED_ROOTS to permit it."
        )
    if not p.is_dir():
        raise ChangeLensError(f"{p} is not a directory")
    return p


def _git(repo: Path, *args: str, check: bool = True) -> str:
    cmd = [
        "git", "--no-optional-locks",
        "-c", "core.fsmonitor=false",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.pager=cat",
        "-c", "diff.external=",
        "-C", str(repo), *args,
    ]
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_EXTERNAL_DIFF="", GIT_PAGER="cat")
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, env=env, shell=False)
    except subprocess.TimeoutExpired:
        raise ChangeLensError("git timed out")
    if check and r.returncode != 0:
        raise ChangeLensError(r.stderr.decode(errors="replace").strip()[:300] or "git failed")
    return r.stdout.decode(errors="replace")


def resolve_commit(repo: Path, ref: str) -> str:
    if not REF_RE.match(ref) or ref.startswith("-"):
        raise ChangeLensError(f"Invalid ref: {ref!r}")
    return _git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}").strip()


def toplevel(repo: Path) -> Path:
    top = Path(_git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    if not any(top == r or r in top.parents for r in allowed_roots()):
        raise ChangeLensError("Repository root is outside the allowed roots")
    return top


def changed_files(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    out = _git(repo, "diff", "--name-status", "--no-ext-diff", "--no-textconv", "-M", base, head, "--")
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append((parts[0][0], parts[-1]))
    return files


def file_diff(repo: Path, base: str, head: str, path: str) -> str:
    return _git(repo, "diff", "-U3", "--no-ext-diff", "--no-textconv", "--no-color",
                base, head, "--", path)


def list_files(repo: Path, rev: str) -> list[str]:
    return _git(repo, "ls-tree", "-r", "--name-only", rev).splitlines()


def read_blob(repo: Path, rev: str, path: str) -> str:
    """Read a file as stored at `rev` (not the working tree, so symlinks/untracked
    files can't redirect reads)."""
    size = _git(repo, "cat-file", "-s", f"{rev}:{path}", check=False).strip()
    if not size.isdigit() or int(size) > MAX_BLOB_BYTES:
        return ""
    return _git(repo, "show", f"{rev}:{path}", check=False)


def log_subjects(repo: Path, base: str, head: str, limit: int = 30) -> list[str]:
    out = _git(repo, "log", f"--max-count={limit}", "--format=%h %s", f"{base}..{head}")
    return out.splitlines()
