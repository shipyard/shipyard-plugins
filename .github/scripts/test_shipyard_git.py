"""Fixture tests for hosted/skills/shipyard/scripts/shipyard-git.sh.

Each case builds throwaway git repos (synthetic values only; secret-shaped
strings are split so the repo's own secret scanner doesn't read them), runs one
subcommand the way the hosted skill tells the agent to, and checks its tagged
output and exit code. No output may contain the word "synthetic", which every
planted secret value carries. Exits 1 with one line per failure.
"""

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "hosted/skills/shipyard/scripts/shipyard-git.sh"
SECRET = 'TOKEN = "synthetic' + '12345abc"\n'
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
       "SHIPYARD_GIT_ALLOW_ANY_REMOTE": "1"}
failures = []

# Lines the secret pattern must flag, one or more per branch, and ordinary code
# it must not. Checked with the script's own pattern through grep -E -i.
PATTERN_HITS = [
    "SECRET_KEY = \"synthetic" + "12345\"",
    "AWS_SECRET_ACCESS_KEY=synthetic" + "12345abc",
    "{\"apiKey\": \"synthetic" + "12345\"}",
    "DB_PASS=supersecret" + "value123",
    "DB_PWD=supersecret" + "value123",
    "PRIVATE_KEY=MIIEabcd" + "efgh1234",
    "password = \"p@ssw0rd!" + "xyz\"",
    "token: 'abcdefgh" + "12345'",
    "Authorization: Bearer abc123def" + "456ghi789",
    "DATABASE_URL=postgres://u:synthetic" + "12345@h/db",
    "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
    "key=AKIA" + "ABCDEFGHIJKLMNOP",
    "GITHUB_TOKEN=ghp" + "_" + "a" * 24,
    "GITLAB=glpat" + "-" + "a" * 20,
    "SLACK=xox" + "b-1234",
    "STRIPE=rk" + "_live_abc",
    "STRIPE=sk" + "_live_abc",
    "OPENAI=sk" + "-" + "a" * 24,
    "GOOGLE=AIza" + "A" * 32,
    "JWT=eyJ" + "a" * 12 + ".b",
    "HOOK=https://hooks.slack" + ".com/services/T0/B0/x",
    "client-key-data: LS0tLS1" + "CRUdJTiBSU0E",
]
PATTERN_MISSES = [
    "const token = process.env.API_TOKEN",
    "api_key = os.environ[\"OPENAI_API_KEY\"]",
    "token = get_token(request)",
    "password_hint_text = None",
    "DATABASE_URL: postgres://app:app@postgres:5432/app",
    "POSTGRES_PASSWORD: app",
    "PASSWORD: ${WORKER_PASSWORD}",
    "passwordField = document.getElementById('pw')",
]


def sh(cwd, *args, check=True, env=None):
    r = subprocess.run(args, cwd=cwd, text=True, capture_output=True, env=env or ENV)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args}: {r.stderr.strip()}")
    return r


def tool(cwd, *args, env=None):
    return sh(cwd, "bash", str(SCRIPT), *args, check=False, env=env)


def repo(path, identity=True):
    path.mkdir(parents=True, exist_ok=True)
    sh(path, "git", "init", "-q", "-b", "main")
    if identity:
        sh(path, "git", "config", "user.name", "t")
        sh(path, "git", "config", "user.email", "t@example.invalid")
    return path


def write(path, text, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode) as f:
        f.write(text)


def commit_all(cwd, msg):
    sh(cwd, "git", "add", "-A")
    sh(cwd, "git", "commit", "-qm", msg)


def lst(tmp, name, *paths):
    """A path-list file, as the agent writes it with its file tool."""
    p = tmp / f"{name}.paths"
    p.write_text("".join(x + "\n" for x in paths))
    return str(p)


def msg(tmp, name, text):
    p = tmp / f"{name}.msg"
    p.write_text(text)
    return str(p)


def expect(name, r, code, present=(), absent=()):
    out = r.stdout
    problems = []
    if r.returncode != code:
        problems.append(f"exit {r.returncode}, want {code}")
    problems += [f"missing {p!r}" for p in present if p not in out]
    problems += [f"unexpected {p!r}" for p in absent if p in out]
    if "synthetic" in out or "synthetic" in r.stderr:
        problems.append("printed a secret value")
    if problems:
        failures.append(f"{name}: {'; '.join(problems)}\n  stdout: {out.strip()[:500]}\n  stderr: {r.stderr.strip()[:200]}")


def bare(t, name, hook=None):
    sh(t, "git", "init", "-q", "--bare", "-b", "main", name)
    if hook:
        h = t / name / "hooks" / "pre-receive"
        h.write_text(hook)
        h.chmod(0o755)
    return f"../{name}"


def pattern_cases():
    found = re.findall(r"^P='([^']+)'$", SCRIPT.read_text(), re.M)
    if len(found) != 1:
        failures.append(f"the script needs exactly one P='...' pattern line, found {len(found)}")
        return
    for line, want in [(s, True) for s in PATTERN_HITS] + [(s, False) for s in PATTERN_MISSES]:
        rc = subprocess.run(["grep", "-qIE", "-i", "-e", found[0]], input=line + "\n", text=True).returncode
        if rc > 1:
            failures.append(f"grep rejects the pattern (exit {rc})")
            return
        if (rc == 0) != want:
            failures.append(f"pattern {'misses' if want else 'flags'} case {line[:14]!r}...")


def main():
    if not SCRIPT.is_file():
        print(f"::error::{SCRIPT.relative_to(ROOT)} is missing")
        return 1
    pattern_cases()
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        L = t / "lists"
        L.mkdir()

        # Paths with shell and glob characters are screened, and only they are committed.
        r1 = repo(t / "brackets")
        write(r1 / "pages/[id].tsx", SECRET)
        write(r1 / "app/(auth)/page.tsx", "export default 1\n")
        write(r1 / "pages/i.tsx", "export default 2\n")
        write(r1 / "PATHS", "x\n")
        expect("screen flags content in a [bracket] path",
               tool(r1, "screen", lst(L, "b1", "pages/[id].tsx", "app/(auth)/page.tsx", "PATHS")),
               1, ["CONTENT_HIT pages/[id].tsx", "SCREEN_HITS"], ["CONTENT_HIT app/(auth)/page.tsx", "SCREEN_CLEAN"])
        expect("commit takes paths literally",
               tool(r1, "commit", lst(L, "b2", "pages/[id].tsx", "app/(auth)/page.tsx"), msg(L, "b2", "add pages\n")),
               0, ["COMMITTED"])
        left = sh(r1, "git", "status", "--porcelain", "--untracked-files=all").stdout
        if "?? pages/i.tsx" not in left:
            failures.append(f"commit staged pages/i.tsx, which matches the glob pages/[id].tsx: {left!r}")

        # The message is taken literally: nothing in it is expanded by a shell.
        write(r1 / "m.txt", "m\n")
        env = {**ENV, "DB_PASSWORD": "synthetic" + "99"}
        expect("commit message is not shell-expanded",
               tool(r1, "commit", lst(L, "m", "m.txt"), msg(L, "m", "use ${DB_PASSWORD} and `id` from Shipyard\n"), env=env),
               0, ["COMMITTED"])
        subject = sh(r1, "git", "log", "-1", "--format=%s").stdout.strip()
        if subject != "use ${DB_PASSWORD} and `id` from Shipyard":
            failures.append(f"commit message was changed on the way in: {subject[:60]!r}")

        # An empty list never commits whatever happens to be staged; a folder or a
        # never-commit name is refused by commit itself.
        write(r1 / "staged.txt", "x\n")
        sh(r1, "git", "add", "staged.txt")
        expect("commit refuses an empty list", tool(r1, "commit", lst(L, "e"), msg(L, "e", "x\n")), 2, ["SCREEN_FAILED"], ["COMMITTED"])
        write(r1 / "src/.env.production", "A=1\n")
        expect("commit refuses a folder", tool(r1, "commit", lst(L, "f", "src"), msg(L, "f", "x\n")), 2, ["SCREEN_FAILED"], ["COMMITTED"])
        expect("commit refuses a never-commit name", tool(r1, "commit", lst(L, "n", "src/.env.production"), msg(L, "n", "x\n")),
               2, ["SCREEN_FAILED"], ["COMMITTED"])

        # A failing commit leaves the user's index as it was.
        before = sh(r1, "git", "diff", "--cached", "--name-only").stdout
        hook = r1 / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\necho 'error: lint failed' >&2\nexit 1\n")
        hook.chmod(0o755)
        write(r1 / "h.txt", "h\n")
        expect("commit reports a failing hook", tool(r1, "commit", lst(L, "h", "h.txt"), msg(L, "h", "x\n")),
               2, ["SCREEN_FAILED", "lint failed"], ["COMMITTED"])
        after = sh(r1, "git", "diff", "--cached", "--name-only").stdout
        if before != after:
            failures.append(f"a failed commit changed the index: {before!r} -> {after!r}")
        hook.unlink()

        # No identity, and a merge in progress, stop before anything changes.
        r0 = repo(t / "noident", identity=False)
        write(r0 / "a.txt", "a\n")
        expect("commit needs an identity", tool(r0, "commit", lst(L, "i", "a.txt"), msg(L, "i", "x\n")), 2, ["NO_IDENTITY"])
        rm = repo(t / "merging")
        write(rm / "c.txt", "base\n"); commit_all(rm, "base")
        sh(rm, "git", "switch", "-qc", "side"); write(rm / "c.txt", "side\n"); commit_all(rm, "side")
        sh(rm, "git", "switch", "-q", "main"); write(rm / "c.txt", "main\n"); commit_all(rm, "main")
        sh(rm, "git", "merge", "-q", "side", check=False)
        expect("commit refuses during a merge", tool(rm, "commit", lst(L, "mg", "c.txt"), msg(L, "mg", "x\n")), 2, ["IN_PROGRESS"])
        expect("inventory shows unmerged files", tool(rm, "inventory"), 0, ["UNMERGED c.txt"])

        # Never-commit names (any case), deletions, binary files and folders.
        r2 = repo(t / "names")
        write(r2 / "keep.txt", "x\n")
        write(r2 / "old/compose.yml", "services: {}\n")
        commit_all(r2, "base")
        (r2 / "old/compose.yml").unlink()
        for name in (".env", ".ENV", "CERT.PFX", "prod.env", "config/.env.local", ".env.example", "app.keystore"):
            write(r2 / name, "A=1\n")
        write(r2 / "blob.bin", "\x00" + SECRET)
        expect("screen flags never-commit names in any case, skips deletions, reports binaries",
               tool(r2, "screen", lst(L, "n2", ".env", ".ENV", "CERT.PFX", "prod.env", "config/.env.local", ".env.example",
                                      "app.keystore", "old/compose.yml", "blob.bin")),
               1, ["NAME_HIT .env\n", "NAME_HIT .ENV", "NAME_HIT CERT.PFX", "NAME_HIT prod.env", "NAME_HIT config/.env.local",
                   "NAME_HIT app.keystore", "DELETED old/compose.yml", "BINARY_NOT_SCANNED blob.bin"],
               ["NAME_HIT .env.example", "SCREEN_FAILED"])
        expect("screen rejects a folder", tool(r2, "screen", lst(L, "d", "config")), 2, ["SCREEN_FAILED"])
        r = tool(r2, "inventory")
        # (.ENV and .env are one file on a case-insensitive filesystem, so only .env is listed there.)
        expect("inventory tags", r, 0, ["NEVER CERT.PFX", "NEVER prod.env", "DELETED old/compose.yml", "FILE blob.bin"],
               ["FILE .env\n", "FILE CERT.PFX"])
        sh(r2, "git", "mv", "keep.txt", "moved.txt")
        expect("inventory reports both sides of a rename", tool(r2, "inventory"), 0, ["DELETED keep.txt", "FILE moved.txt"])

        # File names can't forge output lines.
        rn = repo(t / "badname")
        write(rn / "ok.js\nFILE fake.js", "x\n")
        expect("inventory flags a name with a newline", tool(rn, "inventory"), 0, ["BAD_NAME"], ["\nFILE fake.js"])

        # History: merge-only secrets, other remotes, push URLs, stale tracking refs.
        r3 = repo(t / "merge")
        write(r3 / "c.txt", "base\n"); commit_all(r3, "base")
        sh(r3, "git", "switch", "-qc", "side"); write(r3 / "c.txt", "side\n"); commit_all(r3, "side")
        sh(r3, "git", "switch", "-q", "main"); write(r3 / "c.txt", "main\n"); commit_all(r3, "main")
        sh(r3, "git", "merge", "-q", "side", check=False)
        write(r3 / "c.txt", SECRET); commit_all(r3, "merge")
        expect("screen sees a secret added only in a merge", tool(r3, "screen", "--new-repo", lst(L, "e3")), 1, ["HISTORY_CONTENT_HIT", "c.txt"])

        r4 = repo(t / "other")
        write(r4 / "s.py", SECRET); commit_all(r4, "a")
        sh(r4, "git", "remote", "add", "gitlab", bare(t, "other.git"))
        sh(r4, "git", "push", "-q", "gitlab", "main")
        expect("history already on another remote is still screened for a new repo",
               tool(r4, "screen", "--new-repo", lst(L, "e4")), 1, ["HISTORY_CONTENT_HIT", "s.py", "RANGE whole history"])

        r5 = repo(t / "pushurl")
        write(r5 / "s.py", SECRET); commit_all(r5, "a")
        sh(r5, "git", "remote", "add", "origin", bare(t, "fetch.git"))
        sh(r5, "git", "push", "-q", "origin", "main")
        expect("commits the destination already has are skipped", tool(r5, "screen", lst(L, "e5")), 0, ["SCREEN_CLEAN"], ["HISTORY_CONTENT_HIT"])
        write(r5 / "t.py", SECRET); commit_all(r5, "b")
        sh(r5, "git", "remote", "add", "backup", bare(t, "backup.git"))
        sh(r5, "git", "push", "-q", "backup", "main")
        expect("commits on another remote but not the destination are screened", tool(r5, "screen", lst(L, "e5b")), 1,
               ["HISTORY_CONTENT_HIT", "t.py"])
        sh(r5, "git", "remote", "set-url", "--push", "origin", bare(t, "push.git"))
        expect("a different push URL is what counts", tool(r5, "screen", lst(L, "e5c")), 1, ["HISTORY_CONTENT_HIT", "s.py"])

        rs = repo(t / "stale")
        write(rs / "s.py", SECRET); commit_all(rs, "a")
        sh(rs, "git", "remote", "add", "origin", bare(t, "old.git"))
        sh(rs, "git", "push", "-q", "origin", "main")
        sh(rs, "git", "remote", "set-url", "origin", bare(t, "new.git"))
        sh(rs, "git", "fetch", "-q", "origin", check=False)
        expect("a stale origin/* ref doesn't hide commits", tool(rs, "screen", lst(L, "es")), 1, ["HISTORY_CONTENT_HIT", "s.py"])

        # Unpushed commits are listed for the proposal; secret-looking subjects are hidden.
        r6 = repo(t / "unpushed")
        write(r6 / ".env", "A=1\n"); commit_all(r6, "add env")
        (r6 / ".env").unlink(); commit_all(r6, "drop env")
        write(r6 / "café/.env", "A=1\n"); commit_all(r6, "API_KEY=synthetic" + "12345abc")
        r = tool(r6, "screen", "--new-repo", lst(L, "e6"))
        expect("unpushed commits, deleted and non-ASCII never-commit names", r, 1,
               ["COMMIT ", "COMMITS_TOTAL 3", "HISTORY_NAME_HIT", " .env\n", "café/.env", "subject hidden"])

        # Destination: credentials in URLs are never printed; non-GitHub is named.
        rd = repo(t / "dest")
        sh(rd, "git", "remote", "add", "origin", "https://user:synthetic" + "99@github.com/o/r.git")
        expect("destination hides URL credentials", tool(rd, "destination", env={**ENV, "SHIPYARD_GIT_ALLOW_ANY_REMOTE": ""}), 0,
               ["PUSH_TO https://***@github.com/o/r.git"])
        sh(rd, "git", "remote", "set-url", "origin", "git@gitlab.com:o/r.git")
        expect("destination names a non-GitHub remote", tool(rd, "destination", env={**ENV, "SHIPYARD_GIT_ALLOW_ANY_REMOTE": ""}), 0,
               ["NOT_GITHUB"])

        # Inventory works before git init, honors .gitignore, collapses dependency folders, and is quick.
        r7 = t / "plain"
        write(r7 / "src/app.py", "x\n")
        write(r7 / "build/Dockerfile", "FROM scratch\n")
        write(r7 / ".gitignore", "ignored.txt\n")
        write(r7 / "ignored.txt", "x\n")
        for i in range(4000):
            write(r7 / f"node_modules/pkg{i}/index.js", "x\n")
        start = time.monotonic()
        r = tool(r7, "inventory")
        took = time.monotonic() - start
        expect("inventory in a folder that isn't a repo", r, 0,
               ["NOT_A_REPO", "FILE src/app.py", "FILE build/Dockerfile", "FOLDER node_modules/ 4000 files"],
               ["ignored.txt", "node_modules/pkg0"])
        # gstack-timing-ok: a generous bound that only a per-file subprocess loop (about 40s here) can exceed.
        if took > 20:
            failures.append(f"inventory took {took:.0f}s for 4000 dependency files")
        if (r7 / ".git").exists():
            failures.append("inventory created .git in a folder that wasn't a repo")
        nested = r1 / "sub"
        nested.mkdir()
        expect("inventory refuses a folder inside another repo", tool(nested, "inventory"), 2, ["PARENT_REPO"])
        expect("screen refuses a folder inside another repo", tool(nested, "screen", lst(L, "ns")), 2, ["PARENT_REPO"])

        # Network subcommands against local bare remotes.
        r8 = repo(t / "net")
        expect("fetch without origin", tool(r8, "fetch"), 2, ["NO_ORIGIN"])
        sh(r8, "git", "remote", "add", "origin", bare(t, "empty.git"))
        expect("an empty origin reads as empty", tool(r8, "default-branch"), 0, ["EMPTY_REMOTE"])
        sh(r8, "git", "commit", "-q", "--allow-empty", "-m", "1")
        expect("push the current branch", tool(r8, "push", "main"), 0, ["PUSHED main"])
        expect("default branch after the first push", tool(r8, "default-branch"), 0, ["DEFAULT main"])
        expect("push refuses a branch HEAD isn't on", tool(r8, "push", "other"), 2, ["SCREEN_FAILED"])
        sh(t, "git", "clone", "-q", "empty.git", "clone2")
        c2 = t / "clone2"
        sh(c2, "git", "config", "user.email", "t@example.invalid"); sh(c2, "git", "config", "user.name", "t")
        sh(c2, "git", "commit", "-q", "--allow-empty", "-m", "2"); sh(c2, "git", "push", "-q", "origin", "main")
        sh(r8, "git", "commit", "-q", "--allow-empty", "-m", "3")
        expect("a non-fast-forward push is rejected", tool(r8, "push", "main"), 2, ["PUSH_REJECTED"])
        r9 = repo(t / "blocked")
        sh(r9, "git", "remote", "add", "origin",
           bare(t, "blocked.git", "#!/bin/sh\necho 'GH013: Repository rule violations found. Push cannot contain secrets' >&2\nexit 1\n"))
        sh(r9, "git", "commit", "-q", "--allow-empty", "-m", "1")
        expect("a push blocked for a secret says so", tool(r9, "push", "main"), 2, ["PUSH_BLOCKED"], ["PUSH_REJECTED"])

    for f in failures:
        print(f"::error::{f}")
    return 1 if failures else 0


def test_shipyard_git():
    """The same checks, for pytest runners."""
    assert main() == 0, "\n".join(failures)


if __name__ == "__main__":
    sys.exit(main())
