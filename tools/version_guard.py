"""Keep the number shown in the PAROL6 window in sync with published changes.

The window reads the VERSION file. A software change must not be committed or
pushed while that number stays the same. If the change forgets it, this script
raises the patch number and includes it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERSION_PATH = REPO / "VERSION"
HOOKS_SRC = REPO / ".githooks"
SKIP_DIRS = (".cursor/", ".githooks/", ".vscode/", "tools/")
SKIP_FILES = {"VERSION", ".gitattributes", ".gitignore"}
ZERO_SHA = "0" * 40
FALLBACK_NAME = "kiparis"
FALLBACK_EMAIL = "138474161+MoiOrg@users.noreply.github.com"
SESSION_CONTEXT = (
    "Logiciel PAROL6, dossier « PAROL6 wiring test » : le numero en haut de la "
    "fenetre est le fichier VERSION. Toute modification du programme "
    "(sensor_gui.py, PAROL6.vbs, firmware, JSON, README) doit partir avec un "
    "patch strictement superieur a HEAD, dans le meme commit. S'il est deja "
    "superieur, ne pas l'augmenter une seconde fois. Ne jamais utiliser "
    "--no-verify et ne jamais redescendre VERSION."
)


class GuardError(RuntimeError):
    pass


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "pre-commit":
        if os.environ.get("PAROL6_VERSION_GUARD") == "1":
            return 0
        ensure_commit()
        return 0
    if mode == "pre-push":
        return ensure_pre_push()
    if mode == "cursor-before-shell":
        cursor_before_shell()
        return 0
    if mode == "cursor-stop":
        cursor_stop()
        return 0
    if mode == "session-start":
        session_start()
        return 0
    if mode == "install-git-hooks":
        install_git_hooks()
        return 0
    if mode == "self-test":
        return self_test()
    print(
        "usage: version_guard.py pre-commit|pre-push|cursor-before-shell|"
        "cursor-stop|session-start|install-git-hooks|self-test",
        file=sys.stderr,
    )
    return 2


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def read_json_stdin() -> dict:
    raw = sys.stdin.buffer.read()
    if not raw or not raw.strip():
        return {}
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    elif b"\x00" in raw[:200]:
        text = raw.decode("utf-16-le")
    else:
        text = raw.decode("utf-8-sig")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise GuardError("Le JSON du hook n'est pas un objet.")
    return data


def run_git(args: list[str], *, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise GuardError(detail or f"git {' '.join(args)} a echoue")
    return result


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    if text is None:
        return None
    cleaned = text.strip().lstrip("\ufeff")
    if not re.fullmatch(r"\d+\.\d+\.\d+", cleaned):
        return None
    major, minor, patch = (int(part) for part in cleaned.split("."))
    return major, minor, patch


def format_version(parts: tuple[int, int, int]) -> str:
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def next_version(text: str) -> str:
    parsed = parse_version(text)
    if not parsed:
        return "1.0.0"
    major, minor, patch = parsed
    return format_version((major, minor, patch + 1))


def read_version_file() -> str:
    if not VERSION_PATH.is_file():
        return ""
    return VERSION_PATH.read_text(encoding="utf-8")


def write_version(version: str) -> None:
    if parse_version(version) is None:
        raise GuardError(f"Numero illisible : {version!r}. Attendu : X.Y.Z")
    VERSION_PATH.write_text(version + "\n", encoding="utf-8", newline="\n")


def show_version(rev: str) -> str:
    result = run_git(["show", f"{rev}:VERSION"], check=False)
    if result.returncode != 0:
        return ""
    return result.stdout


def index_version() -> str:
    result = run_git(["show", ":VERSION"], check=False)
    if result.returncode != 0:
        return ""
    return result.stdout


def has_head() -> bool:
    return run_git(["rev-parse", "--verify", "HEAD"], check=False).returncode == 0


def normalize_path(path: str) -> str:
    cleaned = path.strip()
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1]
    if " -> " in cleaned:
        cleaned = cleaned.split(" -> ")[-1].strip()
    return cleaned.replace("\\", "/")


def is_software_path(path: str) -> bool:
    normalized = normalize_path(path)
    if not normalized or normalized in SKIP_FILES:
        return False
    return not any(normalized.startswith(prefix) for prefix in SKIP_DIRS)


def staged_paths() -> list[str]:
    result = run_git(["diff", "--cached", "--name-only"], check=False)
    return [normalize_path(line) for line in result.stdout.splitlines() if line.strip()]


def worktree_software_changes() -> list[str]:
    result = run_git(["status", "--porcelain", "-uall"], check=False)
    found = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = normalize_path(line[3:])
        if is_software_path(path):
            found.append(path)
    return found


def choose_version(previous_text: str, work_text: str, floor_text: str = "") -> str:
    previous = parse_version(previous_text)
    work = parse_version(work_text)
    floor = parse_version(floor_text)
    bases = [item for item in (previous, floor) if item is not None]
    base = max(bases) if bases else None
    incremented = parse_version(next_version(format_version(base) if base else ""))
    if incremented is None:
        raise GuardError("Impossible de calculer le prochain numero.")
    floor_value = base or (0, 0, 0)
    if work is not None and work > floor_value and work >= incremented:
        return format_version(work)
    return format_version(incremented)


def ensure_commit() -> None:
    if not any(is_software_path(path) for path in staged_paths()):
        return
    previous = show_version("HEAD") if has_head() else ""
    if previous and parse_version(previous) is None:
        raise GuardError(f"VERSION du commit precedent est illisible : {previous.strip()!r}")
    target = choose_version(previous, read_version_file())
    before = parse_version(index_version())
    write_version(target)
    run_git(["add", "--", "VERSION"])
    if before != parse_version(target):
        old = format_version(parse_version(previous)) if parse_version(previous) else "aucun"
        print(f"VERSION : {old} -> {target}", file=sys.stderr)


def names_between(remote_sha: str, local_sha: str) -> list[str]:
    if remote_sha == ZERO_SHA:
        result = run_git(["ls-tree", "-r", "--name-only", local_sha])
        return [normalize_path(line) for line in result.stdout.splitlines() if line.strip()]
    result = run_git(["diff", "--name-only", remote_sha, local_sha])
    return [normalize_path(line) for line in result.stdout.splitlines() if line.strip()]


def needs_publish_bump(local_sha: str, remote_sha: str) -> bool:
    if not local_sha or local_sha == remote_sha or local_sha == ZERO_SHA:
        return False
    local = parse_version(show_version(local_sha))
    if remote_sha == ZERO_SHA:
        return not (local is not None and local > (0, 0, 0))
    if not any(is_software_path(path) for path in names_between(remote_sha, local_sha)):
        return False
    remote = parse_version(show_version(remote_sha)) or (0, 0, 0)
    return not (local is not None and local > remote)


def git_operation_in_progress() -> bool:
    git_dir = Path(run_git(["rev-parse", "--absolute-git-dir"]).stdout.strip())
    return any((git_dir / name).exists() for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD"))


def real_index_path() -> Path:
    override = os.environ.get("GIT_INDEX_FILE")
    if override:
        return Path(override)
    git_dir = Path(run_git(["rev-parse", "--absolute-git-dir"]).stdout.strip())
    return git_dir / "index"


def author_identity() -> tuple[str, str]:
    result = run_git(["log", "-1", "--format=%an%n%ae"], check=False)
    lines = result.stdout.splitlines()
    if result.returncode == 0 and len(lines) >= 2 and lines[1].endswith("noreply.github.com"):
        return lines[0], lines[1]
    return FALLBACK_NAME, FALLBACK_EMAIL


def commit_version_keeping_index(version: str) -> None:
    if not has_head():
        raise GuardError("Pas encore de commit : fais un commit, le numero sera ajoute.")
    if git_operation_in_progress():
        raise GuardError("Un merge ou un rebase est en cours. Termine-le avant de pousser.")
    write_version(version)
    index_path = real_index_path()
    backup = index_path.with_name(index_path.name + ".version-guard-backup")
    temporary = index_path.with_name(index_path.name + ".version-guard-tmp")
    if index_path.exists():
        shutil.copy2(index_path, backup)
    name, email = author_identity()
    env = os.environ.copy()
    env["PAROL6_VERSION_GUARD"] = "1"
    env["GIT_INDEX_FILE"] = str(temporary)
    env["GIT_AUTHOR_NAME"] = name
    env["GIT_AUTHOR_EMAIL"] = email
    env["GIT_COMMITTER_NAME"] = name
    env["GIT_COMMITTER_EMAIL"] = email
    try:
        if temporary.exists():
            temporary.unlink()
        subprocess.run(["git", "read-tree", "HEAD"], cwd=REPO, env=env, check=True, capture_output=True)
        subprocess.run(["git", "add", "--", "VERSION"], cwd=REPO, env=env, check=True, capture_output=True)
        tree = subprocess.run(
            ["git", "write-tree"],
            cwd=REPO,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        commit = subprocess.run(
            [
                "git",
                "-c",
                "commit.gpgsign=false",
                "commit-tree",
                tree,
                "-p",
                "HEAD",
                "-m",
                f"Monte le numero affiche a {version}.",
            ],
            cwd=REPO,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        run_git(["update-ref", "HEAD", commit])
    except (subprocess.CalledProcessError, GuardError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        raise GuardError(detail.strip() or str(exc)) from exc
    finally:
        if backup.exists():
            shutil.copy2(backup, index_path)
            backup.unlink()
        if temporary.exists():
            temporary.unlink()
    run_git(["add", "--", "VERSION"])


def upstream_range() -> tuple[str, str] | None:
    if not has_head():
        return None
    head = run_git(["rev-parse", "HEAD"]).stdout.strip()
    upstream = run_git(["rev-parse", "@{u}"], check=False)
    if upstream.returncode == 0:
        return upstream.stdout.strip(), head
    origin = run_git(["rev-parse", "origin/main"], check=False)
    if origin.returncode == 0:
        return origin.stdout.strip(), head
    return None


def publish_bump_if_needed() -> str | None:
    found = upstream_range()
    if found is None:
        return None
    remote_sha, local_sha = found
    if not needs_publish_bump(local_sha, remote_sha):
        return None
    remote_text = "" if remote_sha == ZERO_SHA else show_version(remote_sha)
    target = choose_version(show_version(local_sha), read_version_file(), remote_text)
    commit_version_keeping_index(target)
    print(f"VERSION : {target} ajoute avant le push.", file=sys.stderr)
    return target


def ensure_pre_push() -> int:
    created = False
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local_sha, remote_sha = parts[1], parts[3]
        if not needs_publish_bump(local_sha, remote_sha):
            continue
        remote_text = "" if remote_sha == ZERO_SHA else show_version(remote_sha)
        target = choose_version(show_version(local_sha), read_version_file(), remote_text)
        commit_version_keeping_index(target)
        print(
            f"VERSION monte a {target}. Le push est interrompu pour inclure ce commit. Relance le push.",
            file=sys.stderr,
        )
        created = True
        break
    return 1 if created else 0


def split_tokens(text: str) -> list[str]:
    return [item.strip("\"'") for item in re.findall(r'"[^"]*"|\'[^\']*\'|\S+', text)]


def git_actions(command: str) -> set[str]:
    actions: set[str] = set()
    pattern = re.compile(r"(?:^|[;&|]\s*)git(?:\.exe)?\b", re.IGNORECASE)
    for match in pattern.finditer(command):
        rest = re.split(r"[;&|\n]", command[match.end() :], maxsplit=1)[0]
        tokens = split_tokens(rest)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in ("-c", "-C") and index + 1 < len(tokens):
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            actions.add(token.lower())
            break
    return actions


def skips_hooks(command: str) -> bool:
    if "--no-verify" in command:
        return True
    return bool(re.search(r"\bgit(?:\.exe)?\b[^;&|\n]*\s-n(?:\s|$)", command, re.IGNORECASE))


def command_targets_repo(command: str, cwd: str) -> bool:
    repo = REPO.resolve()
    candidates: list[Path] = []
    if cwd:
        candidates.append(Path(cwd))
    for match in re.finditer(r"(?:^|\s)-C\s+(\"[^\"]+\"|'[^']+'|\S+)", command):
        raw = match.group(1).strip("\"'")
        path = Path(raw)
        if not path.is_absolute():
            path = Path(cwd or os.getcwd()) / path
        candidates.append(path)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == repo or repo in resolved.parents:
            return True
    return False


def deny(message: str) -> None:
    emit(
        {
            "permission": "deny",
            "user_message": "Publication bloquee : le numero affiche n'a pas augmente.",
            "agent_message": message,
        }
    )


def cursor_before_shell() -> None:
    try:
        data = read_json_stdin()
    except Exception:
        emit({"permission": "allow"})
        return
    try:
        command = str(data.get("command") or "")
        cwd = str(data.get("cwd") or "")
        actions = git_actions(command)
        if not actions.intersection({"commit", "push"}):
            emit({"permission": "allow"})
            return
        if not command_targets_repo(command, cwd):
            emit({"permission": "allow"})
            return
        if skips_hooks(command):
            deny(
                "Ne pas utiliser --no-verify. Le fichier VERSION du logiciel PAROL6 "
                "doit augmenter dans le meme commit que la modification."
            )
            return
        install_git_hooks()
        if "push" in actions:
            publish_bump_if_needed()
        emit({"permission": "allow"})
    except Exception as exc:
        command = str(data.get("command") or "")
        cwd = str(data.get("cwd") or "")
        if command_targets_repo(command, cwd):
            deny(f"Le controle VERSION a echoue : {exc}")
        else:
            emit({"permission": "allow"})


def cursor_stop() -> None:
    try:
        data = read_json_stdin()
        if data.get("status") not in (None, "completed"):
            emit({})
            return
        if int(data.get("loop_count") or 0) > 0:
            emit({})
            return
        if not has_head() or not worktree_software_changes():
            emit({})
            return
        previous = show_version("HEAD")
        target = choose_version(previous, read_version_file())
        current = parse_version(read_version_file())
        head = parse_version(previous)
        target_parts = parse_version(target)
        if current is not None and target_parts == current and head is not None and current > head:
            emit({})
            return
        if head is not None and target_parts is not None and not target_parts > head:
            emit({})
            return
        write_version(target)
        emit(
            {
                "followup_message": (
                    f"Le fichier VERSION du logiciel PAROL6 vient d'etre incremente a {target}, "
                    "parce que le programme a change. Ne le modifie plus et ne l'augmente pas "
                    "une seconde fois. S'il reste un commit ou un push a faire, ce fichier doit "
                    "partir dedans. Sinon, laisse-le modifie et indique ce numero."
                )
            }
        )
    except Exception as exc:
        print(f"controle VERSION ignore : {exc}", file=sys.stderr)
        emit({})


def session_start() -> None:
    try:
        install_git_hooks()
    except Exception as exc:
        print(f"installation des hooks git impossible : {exc}", file=sys.stderr)
    try:
        read_json_stdin()
    except Exception:
        pass
    emit({"additional_context": SESSION_CONTEXT})


def bash_path(path: Path) -> str:
    text = str(path)
    if len(text) >= 2 and text[1] == ":":
        text = f"/{text[0].lower()}{text[2:]}"
    return text.replace("\\", "/")


def hook_script(mode: str) -> str:
    python = bash_path(Path(sys.executable))
    return f"""#!/bin/sh
HOOK_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
case "$HOOK_DIR" in
  */.git/hooks)
    ROOT=$(CDPATH= cd -- "$HOOK_DIR/../.." && pwd)
    ;;
  *)
    ROOT=$(CDPATH= cd -- "$HOOK_DIR/.." && pwd)
    ;;
esac
cd "$ROOT" || exit 1
if [ -f "{python}" ]; then
  "{python}" tools/version_guard.py {mode}
  exit $?
fi
if command -v py >/dev/null 2>&1; then
  py -3.11 tools/version_guard.py {mode}
  exit $?
fi
echo "Python introuvable, publication bloquee pour garder le numero a jour." >&2
exit 1
"""


def install_git_hooks() -> None:
    git_dir = REPO / ".git"
    if not git_dir.is_dir():
        return
    destination = git_dir / "hooks"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("pre-commit", "pre-push"):
        (destination / name).write_bytes(hook_script(name).encode("utf-8"))


def self_test() -> int:
    assert parse_version("1.0.3\n") == (1, 0, 3)
    assert parse_version("v1.0.3") is None
    assert next_version("") == "1.0.0"
    assert next_version("1.0.3") == "1.0.4"
    assert choose_version("1.0.0", "1.0.3") == "1.0.3"
    assert choose_version("1.0.0", "1.0.0") == "1.0.1"
    assert choose_version("1.0.0", "1.0.0", "1.0.3") == "1.0.4"
    assert git_actions('git status') == {"status"}
    assert git_actions('git -c user.name=kiparis -c user.email=a@b.c commit -m "x"') == {"commit"}
    assert git_actions('git add .; git commit -m "x"') == {"add", "commit"}
    assert "push" in git_actions("git push origin main")
    assert git_actions("echo git commit") == set()
    assert skips_hooks("git commit --no-verify -m x")
    assert skips_hooks("git commit -n -m x")
    assert not skips_hooks('git commit -m "version -n"')
    assert not is_software_path("tools/version_guard.py")
    assert not is_software_path(".cursor/rules/version-affichage.mdc")
    assert is_software_path("sensor_gui.py")
    assert is_software_path("src/wiring_test.cpp")

    root = Path(tempfile.mkdtemp(prefix="parol version "))
    try:
        _repo_test(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("ok")
    return 0


def _run(args: list[str], cwd: Path, *, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise GuardError((result.stderr or result.stdout or f"{args} a echoue").strip())
    return result


def _repo_test(root: Path) -> None:
    script = Path(__file__).resolve()
    tools = root / "tools"
    hooks = root / ".githooks"
    tools.mkdir()
    hooks.mkdir()
    shutil.copy2(script, tools / "version_guard.py")
    for name in ("pre-commit", "pre-push"):
        shutil.copy2(HOOKS_SRC / name, hooks / name)
    _run(["git", "-c", "init.defaultBranch=main", "init"], root)
    _run([sys.executable, str(tools / "version_guard.py"), "install-git-hooks"], root)
    hook = (root / ".git" / "hooks" / "pre-commit").read_bytes()
    assert b"\r" not in hook
    assert b"version_guard.py pre-commit" in hook

    app = root / "sensor_gui.py"
    app.write_text("print(1)\n", encoding="utf-8")
    _run(["git", "add", "--", "sensor_gui.py"], root)
    _commit(root, "ajoute le logiciel")
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.0.0"

    app.write_text("print(2)\n", encoding="utf-8")
    _run(["git", "add", "--", "sensor_gui.py"], root)
    _commit(root, "change le logiciel")
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.0.1"

    app.write_text("print(3)\n", encoding="utf-8")
    (root / "VERSION").write_text("1.2.0\n", encoding="utf-8")
    _run(["git", "add", "--", "sensor_gui.py", "VERSION"], root)
    _commit(root, "saute une version")
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.2.0"

    guard = tools / "version_guard.py"
    guard.write_text(guard.read_text(encoding="utf-8") + "\n# note\n", encoding="utf-8")
    _run(["git", "add", "--", "tools/version_guard.py"], root)
    _commit(root, "regle le controle")
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.2.0"

    app.write_text("print(4)\n", encoding="utf-8")
    _run(["git", "add", "--", "sensor_gui.py"], root)
    _run(["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--no-verify", "-m", "oublie la version"], root)
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.2.0"
    parent = _run(["git", "rev-parse", "HEAD~1"], root).stdout.strip()
    head = _run(["git", "rev-parse", "HEAD"], root).stdout.strip()
    other = root / "pad_map.json"
    other.write_text("{}\n", encoding="utf-8")
    _run(["git", "add", "--", "pad_map.json"], root)
    direct = subprocess.run(
        [sys.executable, str(tools / "version_guard.py"), "pre-push"],
        cwd=root,
        input=f"refs/heads/main {head} refs/heads/main {parent}\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert direct.returncode == 1, direct.stderr
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.2.1"
    staged = _run(["git", "diff", "--cached", "--name-only"], root).stdout.splitlines()
    assert "pad_map.json" in staged
    assert "sensor_gui.py" not in _run(["git", "show", "--name-only", "--format=", "HEAD"], root).stdout.splitlines()
    author = _run(["git", "log", "-1", "--format=%ae"], root).stdout.strip()
    assert author == FALLBACK_EMAIL

    head = _run(["git", "rev-parse", "HEAD"], root).stdout.strip()
    again = subprocess.run(
        [sys.executable, str(tools / "version_guard.py"), "pre-push"],
        cwd=root,
        input=f"refs/heads/main {head} refs/heads/main {parent}\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert again.returncode == 0, again.stderr

    stop = subprocess.run(
        [sys.executable, str(tools / "version_guard.py"), "cursor-stop"],
        cwd=root,
        input='{"status":"completed","loop_count":0}',
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert stop.returncode == 0, stop.stderr
    payload = json.loads(stop.stdout)
    assert "followup_message" in payload
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "1.2.2"

    shell = subprocess.run(
        [sys.executable, str(tools / "version_guard.py"), "cursor-before-shell"],
        cwd=root,
        input=json.dumps({"command": "git commit --no-verify -m x", "cwd": str(root)}),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert json.loads(shell.stdout)["permission"] == "deny"
    other_repo = subprocess.run(
        [sys.executable, str(tools / "version_guard.py"), "cursor-before-shell"],
        cwd=root,
        input=json.dumps({"command": "git commit -m x", "cwd": str(Path.home())}),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert json.loads(other_repo.stdout)["permission"] == "allow"


def _commit(root: Path, message: str) -> None:
    _run(
        ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", message],
        root,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GuardError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
