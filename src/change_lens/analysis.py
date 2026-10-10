"""Heuristic extraction of changed functions and related tests (no execution)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from . import gitops

DEF_PATTERNS = [
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?fn\s+(\w+)"),      # rust
    re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"),                                                         # python
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)"),                 # js/ts
    re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>"),   # arrow
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)"),                                                    # go
    re.compile(r"^(?!\s*(?:return|else|new|throw|await|yield|case|import|match|if|for|while|switch|let|use|loop|in|do)\b)\s*(?:public|private|protected|static|final|override|suspend|\s)*(?:fun|void|[\w<>\[\],]+)\s+(\w+)\s*\([^;]*$"),  # java/kotlin/c-ish
    re.compile(r"^\s*def\s+(?:self\.)?(\w+[?!]?)"),                                                      # ruby
]
KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "new", "else", "match", "fn", "def"}
TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(_test|_spec|\.test|\.spec)\.\w+$|(^|/)test_[^/]*$", re.I)
HUNK_RE = re.compile(r"^@@ .*? @@ ?(.*)$")
MAX_DIFF_CHARS = 120_000
MAX_PER_FILE = 15_000


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH_RE.search(path))


def func_name(line: str) -> str | None:
    for pat in DEF_PATTERNS:
        m = pat.match(line)
        if m and m.group(1) not in KEYWORDS:
            return m.group(1)
    return None


@dataclass
class FileChange:
    path: str
    status: str
    diff: str
    functions: list[str] = field(default_factory=list)
    is_test: bool = False


@dataclass
class TestRef:
    path: str
    reason: str
    names: list[str]


HUNK_POS_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _enclosing(lines: list[str], lineno: int) -> str | None:
    """Name of the function containing 1-based `lineno` (or defined on it)."""
    if not 1 <= lineno <= len(lines):
        return None
    line = lines[lineno - 1]
    if n := func_name(line):
        return n
    if not line.strip():
        return None
    want = _indent(line)
    for i in range(lineno - 2, -1, -1):
        cand = lines[i]
        if cand.strip() and _indent(cand) < want:
            if n := func_name(cand):
                return n
            want = _indent(cand)
            if want == 0:
                return None
    return None


def changed_functions(diff: str, old_text: str = "", new_text: str = "") -> list[str]:
    old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
    found: dict[str, None] = {}
    o = n = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            if m := HUNK_POS_RE.match(line):
                o, n = int(m.group(1)), int(m.group(2))
            if m := HUNK_RE.match(line):
                if name := func_name(m.group(1)):
                    found[name] = None
        elif line.startswith("+") and not line.startswith("+++"):
            if name := _enclosing(new_lines, n):
                found[name] = None
            n += 1
        elif line.startswith("-") and not line.startswith("---"):
            if name := _enclosing(old_lines, o):
                found[name] = None
            o += 1
        elif line.startswith(" "):
            o += 1
            n += 1
    return list(found)


def collect(repo, base_sha: str, head_sha: str):
    changes: list[FileChange] = []
    budget = MAX_DIFF_CHARS
    truncated = False
    for status, path in gitops.changed_files(repo, base_sha, head_sha):
        d = gitops.file_diff(repo, base_sha, head_sha, path)
        if "Binary files" in d[:300]:
            d = "(binary file)"
        old = gitops.read_blob(repo, base_sha, path) if status in "MD" else ""
        new = gitops.read_blob(repo, head_sha, path) if status in "MAR" else ""
        fc = FileChange(path, status, "", changed_functions(d, old, new), is_test_path(path))
        if len(d) > MAX_PER_FILE:
            d = d[:MAX_PER_FILE] + "\n... [diff truncated]"
            truncated = True
        if budget <= 0:
            d = "[omitted: diff budget exhausted]"
            truncated = True
        budget -= len(d)
        fc.diff = d
        changes.append(fc)
    return changes, truncated


def find_tests(repo, head_sha: str, changes: list[FileChange]) -> list[TestRef]:
    """Tests that were changed, or that mention a changed function / module name."""
    refs: dict[str, TestRef] = {}
    for c in changes:
        if c.is_test:
            refs[c.path] = TestRef(c.path, "modified in this change", c.functions)
    targets = {f for c in changes if not c.is_test for f in c.functions if len(f) > 3}
    stems = {PurePosixPath(c.path).stem for c in changes if not c.is_test}
    if not targets and not stems:
        return list(refs.values())
    for path in gitops.list_files(repo, head_sha):
        if path in refs or not is_test_path(path):
            continue
        text = gitops.read_blob(repo, head_sha, path)
        if not text:
            continue
        hit = sorted(t for t in targets if re.search(rf"\b{re.escape(t)}\b", text))
        stem_hit = [s for s in stems if s and s in PurePosixPath(path).stem]
        if hit or stem_hit:
            names = [n for n in (func_name(l) for l in text.splitlines()) if n and n.lower().startswith("test") or n and "test" in n.lower()]
            reason = f"references {', '.join(hit[:5])}" if hit else "name matches changed module"
            refs[path] = TestRef(path, reason, names[:15])
        if len(refs) >= 25:
            break
    return list(refs.values())
