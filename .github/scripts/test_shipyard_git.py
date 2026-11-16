"""Fixture tests for hosted/skills/shipyard/scripts/shipyard-git.sh.

Each case builds throwaway git repos (synthetic values only; secret-shaped
strings are split so the repo's own secret scanner doesn't read them), runs one
subcommand the way the hosted skill tells the agent to, and checks its tagged
output and exit code. No output may contain the word "synthetic", which every
planted secret value carries.

Pushes go to `ssh://git@github.com/<name>.git` through a fake ssh that serves
local bare repos, so the script's GitHub-only guard runs unchanged. Exits 1
with one line per failure; pytest runs it as one test.
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

FAKE_SSH = """#!/bin/sh
# Serve ssh://git@github.com/<name>.git from $FIXTURE_ROOT/<name>.git, locally.
for a; do last=$a; done
case "$last" in
  git-upload-pack*|git-receive-pack*) ;;
  *) exit 0 ;;
esac
cmd=${last%% *}; path=${last#* }; path=${path#\\'}; path=${path%\\'}
exec "$cmd" "$FIXTURE_ROOT$path"
"""


def sh(cwd, *args, check=True, env=None):
    r = subprocess.run(args, cwd=cwd, text=True, capture_output=True, env=env or ENV)
    if check and r.returncode != 0:
        raise RuntimeError(f"{args}: {r.stderr.strip()}")
    return r


def tool(cwd, *args, env=None, timeout=120):
    try:
        return subprocess.run(["bash", str(SCRIPT), *args], cwd=cwd, text=True, capture_output=True,
                              env=env or ENV, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "TIMED_OUT", "")


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


def lst(name, *paths):
    """A path-list file, as the agent writes it with its file tool."""
    p = LISTS / f"{name}.paths"
    p.write_text("".join(x + "\n" for x in paths))
    return str(p)


def msg(name, text):
    p = LISTS / f"{name}.msg"
    p.write_text(text)
    return str(p)


def gh_remote(t, cwd, name, hook=None, add=True):
    """A bare repo served as ssh://git@github.com/<name>.git."""
    sh(t, "git", "init", "-q", "--bare", "-b", "main", str(FIXTURES / f"{name}.git"))
    if hook:
        h = FIXTURES / f"{name}.git" / "hooks" / "pre-receive"
        h.write_text(hook)
        h.chmod(0o755)
    url = f"ssh://git@github.com/{name}.git"
    if add:
        sh(cwd, "git", "remote", "add", "origin", url)
    return url


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


def checkout_rules():
    """Windows checkouts must keep LF in the script, or bash can't parse it."""
    if b"\r" in SCRIPT.read_bytes():
        failures.append("shipyard-git.sh contains a carriage return")
    attrs = ROOT / ".gitattributes"
    if not attrs.is_file() or not re.search(r"^\*\.sh\s+.*eol=lf", attrs.read_text(), re.M):
        failures.append(".gitattributes must set `*.sh text eol=lf`")
    if "SHIPYARD_GIT_ALLOW_ANY_REMOTE" in SCRIPT.read_text():
        failures.append("the script still has a test-only bypass of its GitHub check")


def main():
    if not SCRIPT.is_file():
        print(f"::error::{SCRIPT.relative_to(ROOT)} is missing")
        return 1
    pattern_cases()
    checkout_rules()
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        global LISTS, FIXTURES, ENV
        LISTS = t / "lists"; LISTS.mkdir()
        FIXTURES = t / "github"; FIXTURES.mkdir()
        fake = t / "fake-ssh"; fake.write_text(FAKE_SSH); fake.chmod(0o755)
        ENV = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "SSH_ASKPASS"))}
        ENV.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_SSH_COMMAND": str(fake),
                    "GIT_SSH_VARIANT": "ssh", "FIXTURE_ROOT": str(FIXTURES)})

        # Paths with shell and glob characters are screened, and only they are committed.
        r1 = repo(t / "brackets")
        write(r1 / "pages/[id].tsx", SECRET)
        write(r1 / "app/(auth)/page.tsx", "export default 1\n")
        write(r1 / "pages/i.tsx", "export default 2\n")
        write(r1 / "PATHS", "x\n")
        r = tool(r1, "screen", lst("b1", "pages/[id].tsx", "app/(auth)/page.tsx", "PATHS"))
        expect("screen flags content in a [bracket] path", r, 1, [" pages/[id].tsx\n", "SCREEN_HITS"],
               ["app/(auth)/page.tsx", "SCREEN_CLEAN"])
        confirmed_b1 = "".join(l + "\n" for l in r.stdout.splitlines() if l.startswith("CONTENT_HIT "))
        if not re.fullmatch(r"CONTENT_HIT [0-9a-f]{40} pages/\[id\]\.tsx\n", confirmed_b1):
            failures.append(f"CONTENT_HIT lines must carry the file's blob id: {confirmed_b1!r}")
        write(r1 / "staged.txt", "x\n")
        sh(r1, "git", "add", "staged.txt")
        expect("commit refuses a content hit the user hasn't confirmed",
               tool(r1, "commit", lst("b2", "pages/[id].tsx", "app/(auth)/page.tsx"), msg("b2", "add pages\n")),
               2, ["SCREEN_FAILED"], ["COMMITTED"])
        ok = LISTS / "ok.txt"; ok.write_text(confirmed_b1)
        expect("commit takes paths literally",
               tool(r1, "commit", lst("b2", "pages/[id].tsx", "app/(auth)/page.tsx"), msg("b2", "add pages\n"), str(ok)),
               0, ["COMMITTED"])
        left = sh(r1, "git", "status", "--porcelain", "--untracked-files=all").stdout
        if "?? pages/i.tsx" not in left:
            failures.append(f"commit staged pages/i.tsx, which matches the glob pages/[id].tsx: {left!r}")
        head_files = sh(r1, "git", "show", "--name-only", "--format=", "HEAD").stdout
        if "staged.txt" in head_files:
            failures.append("commit included staged.txt, which was staged before but not listed")

        # The message is taken literally, and a secret-looking message is refused.
        write(r1 / "m.txt", "m\n")
        env = {**ENV, "DB_PASSWORD": "synthetic" + "99"}
        expect("commit message is not shell-expanded",
               tool(r1, "commit", lst("m", "m.txt"), msg("m", "use ${DB_PASSWORD} and `id` from Shipyard\n"), env=env),
               0, ["COMMITTED"])
        subject = sh(r1, "git", "log", "-1", "--format=%s").stdout.strip()
        if subject != "use ${DB_PASSWORD} and `id` from Shipyard":
            failures.append(f"commit message was changed on the way in: {subject[:60]!r}")
        write(r1 / "m2.txt", "m\n")
        expect("commit refuses a secret-looking message",
               tool(r1, "commit", lst("m2", "m2.txt"), msg("m2", "fix\n\nAPI_KEY=synthetic" + "12345abc\n")),
               2, ["SCREEN_FAILED"], ["COMMITTED"])

        # An empty list, a folder, a never-commit name or a dependency folder is refused by commit itself.
        expect("commit refuses an empty list", tool(r1, "commit", lst("e"), msg("e", "x\n")), 2, ["SCREEN_FAILED"], ["COMMITTED"])
        write(r1 / "src/.env.production", "A=1\n")
        expect("commit refuses a folder", tool(r1, "commit", lst("f", "src"), msg("f", "x\n")), 2, ["SCREEN_FAILED"], ["COMMITTED"])
        expect("commit refuses a never-commit name", tool(r1, "commit", lst("n", "src/.env.production"), msg("n", "x\n")),
               2, ["SCREEN_FAILED"], ["COMMITTED"])
        write(r1 / "venv/lib.py", "x\n")
        expect("commit refuses a dependency folder file", tool(r1, "commit", lst("v", "venv/lib.py"), msg("v", "x\n")),
               2, ["SCREEN_FAILED"], ["COMMITTED"])

        # A failing hook leaves the index as it was, and its output isn't printed.
        before = sh(r1, "git", "diff", "--cached", "--name-only").stdout
        hook = r1 / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\necho 'error: lint failed near API_KEY=synthetic" + "12345abc'\n"
                        "echo 'error: lint failed near API_KEY=synthetic" + "12345abc' >&2\nexit 1\n")
        hook.chmod(0o755)
        write(r1 / "h.txt", "h\n")
        expect("commit reports a failing hook without its secret", tool(r1, "commit", lst("h", "h.txt"), msg("h", "x\n")),
               2, ["SCREEN_FAILED", "hidden"], ["COMMITTED"])
        after = sh(r1, "git", "diff", "--cached", "--name-only").stdout
        if before != after:
            failures.append(f"a failed commit changed the index: {before!r} -> {after!r}")
        # A hook that stages extra files gets the commit undone.
        hook.write_text("#!/bin/sh\ngit add -A\n")
        old_head = sh(r1, "git", "rev-parse", "HEAD").stdout.strip()
        write(r1 / "extra.txt", "x\n")
        expect("commit undoes a hook that adds unlisted files", tool(r1, "commit", lst("hx", "h.txt"), msg("hx", "x\n")),
               2, ["SCREEN_FAILED", "extra.txt"], ["COMMITTED"])
        if sh(r1, "git", "rev-parse", "HEAD").stdout.strip() != old_head:
            failures.append("a hook's extra files stayed committed")
        # A hook that writes a secret into a listed file gets the commit undone.
        hook.write_text("#!/bin/sh\nprintf '%s\\n' 'TOKEN = \"synthetic" + "12345abc\"' >> h.txt\ngit add h.txt\n")
        expect("commit undoes a hook that writes a secret", tool(r1, "commit", lst("hy", "h.txt"), msg("hy", "x\n")),
               2, ["SCREEN_FAILED"], ["COMMITTED"])
        if sh(r1, "git", "rev-parse", "HEAD").stdout.strip() != old_head:
            failures.append("a hook's secret stayed committed")
        hook.unlink()

        # No identity, and a merge in progress, stop before anything changes.
        r0 = repo(t / "noident", identity=False)
        write(r0 / "a.txt", "a\n")
        expect("commit needs an identity", tool(r0, "commit", lst("i", "a.txt"), msg("i", "x\n")), 2, ["NO_IDENTITY"])
        rm = repo(t / "merging")
        write(rm / "c.txt", "base\n"); commit_all(rm, "base")
        sh(rm, "git", "switch", "-qc", "side"); write(rm / "c.txt", "side\n"); commit_all(rm, "side")
        sh(rm, "git", "switch", "-q", "main"); write(rm / "c.txt", "main\n"); commit_all(rm, "main")
        sh(rm, "git", "merge", "-q", "side", check=False)
        expect("commit refuses during a merge", tool(rm, "commit", lst("mg", "c.txt"), msg("mg", "x\n")), 2, ["IN_PROGRESS a merge"])
        expect("inventory shows unmerged files", tool(rm, "inventory"), 0, ["UNMERGED c.txt"])

        # Never-commit names (any case), deletions, binaries, symlinks, size.
        r2 = repo(t / "names")
        write(r2 / "keep.txt", "x\n")
        write(r2 / "old/compose.yml", "services: {}\n")
        commit_all(r2, "base")
        (r2 / "old/compose.yml").unlink()
        for name in (".env", ".ENV", "CERT.PFX", "prod.env", "config/.env.local", ".env.example", "app.keystore"):
            write(r2 / name, "A=1\n")
        write(r2 / "blob.bin", "\x00" + SECRET)
        os.symlink("/dev/zero", r2 / "zero")
        os.symlink("missing-target", r2 / "dangling")
        with open(r2 / "huge.bin", "wb") as f:
            f.seek(51 * 1024 * 1024)
            f.write(b"x")
        r = tool(r2, "screen", lst("n2", ".env", ".ENV", "CERT.PFX", "prod.env", "config/.env.local", ".env.example",
                                   "app.keystore", "old/compose.yml", "blob.bin", "zero", "dangling", "huge.bin"), timeout=30)
        expect("screen: never-commit names in any case, deletions, binaries, symlinks, size", r, 1,
               ["NAME_HIT .env\n", "NAME_HIT .ENV", "NAME_HIT CERT.PFX", "NAME_HIT prod.env", "NAME_HIT config/.env.local",
                "NAME_HIT app.keystore", "DELETED old/compose.yml", "BINARY_NOT_SCANNED blob.bin", "TOO_BIG huge.bin"],
               ["NAME_HIT .env.example", "SCREEN_FAILED", "TIMED_OUT", "zero"])
        (r2 / "huge.bin").unlink()
        expect("screen rejects a folder", tool(r2, "screen", lst("d", "config")), 2, ["SCREEN_FAILED"])
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

        # History: merges, other remotes, push URLs, stale refs, attributes, replace refs, odd names, messages.
        r3 = repo(t / "merge")
        write(r3 / "c.txt", "base\n"); commit_all(r3, "base")
        sh(r3, "git", "switch", "-qc", "side"); write(r3 / "c.txt", "side\n"); commit_all(r3, "side")
        sh(r3, "git", "switch", "-q", "main"); write(r3 / "c.txt", "main\n"); commit_all(r3, "main")
        sh(r3, "git", "merge", "-q", "side", check=False)
        write(r3 / "c.txt", SECRET); commit_all(r3, "merge")
        expect("screen sees a secret added only in a merge", tool(r3, "screen", "--new-repo", lst("e3")), 1, ["HISTORY_CONTENT_HIT", "c.txt"])

        r4 = repo(t / "other")
        write(r4 / "s.py", SECRET); commit_all(r4, "a")
        sh(t, "git", "init", "-q", "--bare", "-b", "main", "other.git")
        sh(r4, "git", "remote", "add", "gitlab", "../other.git")
        sh(r4, "git", "push", "-q", "gitlab", "main")
        expect("history already on another remote is still screened for a new repo",
               tool(r4, "screen", "--new-repo", lst("e4")), 1, ["HISTORY_CONTENT_HIT", "s.py", "RANGE whole history"])

        r5 = repo(t / "pushurl")
        write(r5 / "s.py", SECRET); commit_all(r5, "a")
        gh_remote(t, r5, "pushurl")
        sh(r5, "git", "push", "-q", "origin", "main")
        expect("commits the destination already has are skipped", tool(r5, "screen", lst("e5")), 0, ["SCREEN_CLEAN"], ["HISTORY_CONTENT_HIT"])
        write(r5 / "t.py", SECRET); commit_all(r5, "b")
        sh(t, "git", "init", "-q", "--bare", "-b", "main", "backup.git")
        sh(r5, "git", "remote", "add", "backup", "../backup.git")
        sh(r5, "git", "push", "-q", "backup", "main")
        expect("commits on another remote but not the destination are screened", tool(r5, "screen", lst("e5b")), 1,
               ["HISTORY_CONTENT_HIT", "t.py"])
        sh(r5, "git", "remote", "set-url", "--push", "origin", gh_remote(t, r5, "pushurl2", add=False))
        expect("a different push URL is what counts", tool(r5, "screen", lst("e5c")), 1, ["HISTORY_CONTENT_HIT", "s.py"])
        sh(r5, "git", "remote", "set-url", "--add", "--push", "origin", gh_remote(t, r5, "pushurl3", add=False))
        expect("two push URLs screen the whole history", tool(r5, "screen", lst("e5d")), 1, ["more than one URL"])
        expect("push refuses two push URLs", tool(r5, "push", "main"), 2, ["SCREEN_FAILED"], ["PUSHED"])

        rs = repo(t / "stale")
        write(rs / "s.py", SECRET); commit_all(rs, "a")
        gh_remote(t, rs, "old")
        sh(rs, "git", "push", "-q", "origin", "main")
        sh(rs, "git", "remote", "set-url", "origin", gh_remote(t, rs, "new", add=False))
        expect("a stale origin/* ref doesn't hide commits", tool(rs, "screen", lst("es")), 1, ["HISTORY_CONTENT_HIT", "s.py"])

        rh = repo(t / "hidden")
        write(rh / ".gitattributes", "config.txt -diff\n")
        write(rh / "config.txt", SECRET)
        write(rh / "commit notes.txt", SECRET)
        write(rh / "my dir/.env", "A=1\n"); write(rh / "my dir/id_rsa", "x\n")
        commit_all(rh, "a")
        r = tool(rh, "screen", "--new-repo", lst("eh"))
        expect("history: -diff attributes, 'commit ' names and spaced dirs don't hide hits", r, 1,
               ["config.txt", "commit notes.txt", "my dir/.env", "my dir/id_rsa"])

        rr = repo(t / "replaced")
        write(rr / "s.py", SECRET); commit_all(rr, "a")
        bad = sh(rr, "git", "rev-parse", "HEAD").stdout.strip()
        sh(rr, "git", "rm", "-q", "s.py"); write(rr / "ok.txt", "ok\n"); commit_all(rr, "clean")
        clean = sh(rr, "git", "rev-parse", "HEAD").stdout.strip()
        sh(rr, "git", "reset", "-q", "--hard", bad)
        sh(rr, "git", "replace", bad, clean)
        expect("replace refs don't hide a commit", tool(rr, "screen", "--new-repo", lst("er")), 1, ["HISTORY_CONTENT_HIT", "s.py"])

        r6 = repo(t / "unpushed")
        write(r6 / ".env", "A=1\n"); commit_all(r6, "add env")
        (r6 / ".env").unlink(); commit_all(r6, "drop env")
        write(r6 / "café/.env", "A=1\n"); commit_all(r6, "API_KEY=synthetic" + "12345abc")
        write(r6 / "b.txt", "b\n"); sh(r6, "git", "add", "b.txt")
        sh(r6, "git", "commit", "-q", "-m", "body", "-m", "password = \"synthetic" + "12345abc\"")
        r = tool(r6, "screen", "--new-repo", lst("e6"))
        expect("unpushed commits, deleted and non-ASCII names, secret-looking messages", r, 1,
               ["COMMIT ", "COMMITS_TOTAL 4", "HISTORY_NAME_HIT", " .env\n", "café/.env", "subject hidden", "HISTORY_MESSAGE_HIT"])
        if r.stdout.count("COMMITS_TOTAL") != 1:
            failures.append("COMMITS_TOTAL printed more than once")

        # Caps and the no-repo / no-commits cases.
        rc = repo(t / "cap")
        for i in range(205):
            write(rc / f"f{i:03}.txt", "x\n")
        r = tool(rc, "inventory")
        m = re.search(r"INVENTORY_TOTAL 205 \(all of them: ([^)]+)\)", r.stdout)
        if not m or not Path(m.group(1)).is_file() or r.stdout.count("\nFILE ") > 200:
            failures.append(f"inventory over the cap doesn't name a saved full list: {r.stdout[-200:]!r}")
        expect("no commits yet", tool(rc, "screen", lst("nc")), 0, ["NO_COMMITS", "SCREEN_CLEAN"])
        plain = t / "plain-noscreen"; plain.mkdir()
        expect("not a repo yet", tool(plain, "screen", lst("nr")), 0, ["NO_REPO", "SCREEN_CLEAN"])

        # Destination and remotes: credentials never printed; only real GitHub hosts pass.
        rd = repo(t / "dest")
        sh(rd, "git", "remote", "add", "origin", "https://user:synthetic" + "99@github.com/o/r.git")
        sh(rd, "git", "remote", "add", "work", "git@gitlab.com:o/r.git")
        expect("destination hides URL credentials", tool(rd, "destination"), 0, ["PUSH_TO https://***@github.com/o/r.git"])
        expect("remotes hides credentials for every remote", tool(rd, "remotes"), 0,
               ["REMOTE origin GITHUB https://***@github.com/o/r.git", "REMOTE work OTHER git@gitlab.com:o/r.git"])
        for bad_url in ("https://github.com:443@example.invalid/o/r.git", "https://github.com.example.invalid/o/r.git",
                        "../local.git", "git@example.invalid:github.com/o/r.git"):
            sh(rd, "git", "remote", "set-url", "origin", bad_url)
            expect(f"destination rejects {bad_url}", tool(rd, "destination"), 0, ["NOT_GITHUB"], ["PUSH_TO"])
        for good_url in ("git@github.com:o/r.git", "ssh://git@ssh.github.com:443/o/r.git", "https://GitHub.com/o/r.git"):
            sh(rd, "git", "remote", "set-url", "origin", good_url)
            expect(f"destination accepts {good_url}", tool(rd, "destination"), 0, ["PUSH_TO"], ["NOT_GITHUB"])
        sh(rd, "git", "config", "remote.origin.pushurl", "--upload-pack=touch PWNED")
        tool(rd, "screen", lst("inj"))
        if (rd / "PWNED").exists():
            failures.append("a push URL starting with --upload-pack= ran a command")

        # Inventory before git init, .gitignore, dependency folders once each, and fast.
        r7 = t / "plain"
        write(r7 / "src/app.py", "x\n")
        write(r7 / "build/Dockerfile", "FROM scratch\n")
        write(r7 / ".gitignore", "ignored.txt\n")
        write(r7 / "ignored.txt", "x\n")
        for i in range(4000):
            write(r7 / f"node_modules/pkg{i}/index.js", "x\n")
        for i in range(50):
            write(r7 / f"node_modules/pkg{i}/node_modules/dep/index.js", "x\n")
            write(r7 / f".venv/lib/p{i}/__pycache__/m.pyc", "x")
        start = time.monotonic()
        r = tool(r7, "inventory")
        took = time.monotonic() - start
        expect("inventory in a folder that isn't a repo", r, 0,
               ["NOT_A_REPO", "FILE src/app.py", "FILE build/Dockerfile", "FOLDER node_modules/ 4050 files", "FOLDER .venv/ 50 files"],
               ["ignored.txt", "node_modules/pkg0", "__pycache__"])
        # gstack-timing-ok: a generous bound that only a per-file subprocess loop (about 40s here) can exceed.
        if took > 20:
            failures.append(f"inventory took {took:.0f}s for 4,100 dependency files")
        if (r7 / ".git").exists():
            failures.append("inventory created .git in a folder that wasn't a repo")
        many = [f"src/m{i}.py" for i in range(3000)]
        for p in many:
            write(r7 / p, "x = 1\n")
        sh(r7, "git", "init", "-q")
        start = time.monotonic()
        r = tool(r7, "screen", lst("many", *many))
        took = time.monotonic() - start
        expect("screen of 3,000 files", r, 0, ["FILES_CHECKED 3000", "SCREEN_CLEAN"])
        # gstack-timing-ok: a generous bound that only a per-file subprocess loop (about 30s here) can exceed.
        if took > 15:
            failures.append(f"screen took {took:.0f}s for 3,000 files")
        nested = r1 / "sub"
        nested.mkdir()
        expect("inventory refuses a folder inside another repo", tool(nested, "inventory"), 2, ["PARENT_REPO"])
        expect("screen refuses a folder inside another repo", tool(nested, "screen", lst("ns")), 2, ["PARENT_REPO"])

        # Network subcommands against fake-GitHub bare repos.
        r8 = repo(t / "net")
        expect("fetch without origin", tool(r8, "fetch"), 2, ["NO_ORIGIN"])
        gh_remote(t, r8, "netrepo")
        expect("an empty origin reads as empty", tool(r8, "default-branch"), 0, ["EMPTY_REMOTE"])
        sh(r8, "git", "commit", "-q", "--allow-empty", "-m", "1")
        sh(r8, "git", "tag", "-a", "v9", "-m", "release")
        sh(r8, "git", "config", "push.followTags", "true")
        expect("push the current branch", tool(r8, "push", "main"), 0, ["PUSHED main"])
        tags = sh(FIXTURES / "netrepo.git", "git", "tag").stdout
        if "v9" in tags:
            failures.append("push sent a tag the user didn't approve (push.followTags)")
        expect("default branch after the first push", tool(r8, "default-branch"), 0, ["DEFAULT main"])
        expect("push refuses a branch HEAD isn't on", tool(r8, "push", "other"), 2, ["SCREEN_FAILED"])
        sh(t, "git", "clone", "-q", str(FIXTURES / "netrepo.git"), "clone2")
        c2 = t / "clone2"
        sh(c2, "git", "config", "user.email", "t@example.invalid"); sh(c2, "git", "config", "user.name", "t")
        sh(c2, "git", "commit", "-q", "--allow-empty", "-m", "2"); sh(c2, "git", "push", "-q", "origin", "main")
        sh(r8, "git", "commit", "-q", "--allow-empty", "-m", "3")
        expect("a non-fast-forward push is rejected", tool(r8, "push", "main"), 2, ["PUSH_REJECTED"])

        r9 = repo(t / "blocked")
        gh_remote(t, r9, "blocked", "#!/bin/sh\necho 'GH013: Repository rule violations found. Push cannot contain secrets' >&2\nexit 1\n")
        sh(r9, "git", "commit", "-q", "--allow-empty", "-m", "1")
        expect("a push blocked for a secret says so", tool(r9, "push", "main"), 2, ["PUSH_BLOCKED GitHub found a secret"], ["PUSH_REJECTED"])
        r10 = repo(t / "declined")
        gh_remote(t, r10, "declined", "#!/bin/sh\necho 'policy: branch is protected' >&2\nexit 1\n")
        sh(r10, "git", "commit", "-q", "--allow-empty", "-m", "1")
        expect("a push a remote rule declines says so", tool(r10, "push", "main"), 2, ["PUSH_BLOCKED the remote declined"], ["NET_FAILED"])
        r11 = repo(t / "prepush")
        gh_remote(t, r11, "prepush")
        sh(r11, "git", "commit", "-q", "--allow-empty", "-m", "1")
        h = r11 / ".git/hooks/pre-push"; h.write_text("#!/bin/sh\necho 'lint failed' >&2\nexit 1\n"); h.chmod(0o755)
        expect("a failing pre-push hook is named as a hook", tool(r11, "push", "main"), 2, ["HOOK_FAILED"], ["NET_FAILED"])

        # Push re-screens what it sends, unless the user confirmed those exact hits.
        r12 = repo(t / "rescreen")
        gh_remote(t, r12, "rescreen")
        write(r12 / "s.py", SECRET); commit_all(r12, "a")
        r = tool(r12, "push", "main")
        expect("push re-screens the commits it sends", r, 1, ["HISTORY_CONTENT_HIT", "s.py"], ["PUSHED"])
        hits = "".join(line + "\n" for line in r.stdout.splitlines() if line.startswith("HISTORY_"))
        confirmed = LISTS / "confirmed.txt"; confirmed.write_text(hits)
        expect("push goes ahead for hits the user confirmed", tool(r12, "push", "main", str(confirmed)), 0, ["PUSHED main"])


        # --- Pass-2 criticals ---
        # 1. '?' or '#' ends a URL's authority: the host is what git connects to, and is what's shown.
        rq = repo(t / "authority")
        for tricky in ("https://127.0.0.1:9?@github.com/o/r.git", "https://127.0.0.1:9#@github.com/o/r.git"):
            sh(rq, "git", "remote", "remove", "origin", check=False)
            sh(rq, "git", "remote", "add", "origin", tricky)
            r = tool(rq, "destination")
            expect(f"destination rejects {tricky}", r, 0, ["NOT_GITHUB", "127.0.0.1"], ["PUSH_TO", "***@github.com"])
        sh(rq, "git", "remote", "set-url", "origin", "https://u:p@ss" + "synthetic@github.com/o/r.git")
        expect("redaction covers a password with @ in it", tool(rq, "destination"), 0, ["PUSH_TO https://***@github.com/o/r.git"])

        # 2. log.showRoot=false doesn't hide the first commit.
        rt = repo(t / "showroot")
        sh(rt, "git", "config", "log.showRoot", "false")
        sh(rt, "git", "config", "color.ui", "always")
        sh(rt, "git", "config", "log.showSignature", "true")
        write(rt / ".env", "A=1\n"); write(rt / "s.py", SECRET); commit_all(rt, "root")
        expect("the first commit is screened whatever log.showRoot says", tool(rt, "screen", "--new-repo", lst("sr")), 1,
               ["HISTORY_NAME_HIT", ".env", "HISTORY_CONTENT_HIT", "s.py"])

        # 3. Every commit holding a never-commit file is its own hit.
        rv = repo(t / "versions")
        write(rv / ".env", "DB=one\n"); commit_all(rv, "one")
        write(rv / ".env", "DB=two\n"); commit_all(rv, "two")
        r = tool(rv, "screen", "--new-repo", lst("sv"))
        if r.stdout.count("HISTORY_NAME_HIT") != 2:
            failures.append(f"each commit holding .env must be its own hit: {r.stdout!r}")

        # 4. The user's hooks run with their own pathspec and locale settings.
        rk = repo(t / "hookenv")
        hk = rk / ".git/hooks/pre-commit"
        hk.write_text("#!/bin/sh\ngit diff --cached --name-only -- '*.py' > .git/hook-saw\n"
                      "[ -n \"$GIT_LITERAL_PATHSPECS\" ] && echo LITERAL >> .git/hook-saw\nexit 0\n")
        hk.chmod(0o755)
        write(rk / "app.py", "x = 1\n")
        expect("commit with a glob-scoped hook", tool(rk, "commit", lst("hk", "app.py"), msg("hk", "x\n")), 0, ["COMMITTED"])
        saw = (rk / ".git/hook-saw").read_text() if (rk / ".git/hook-saw").exists() else ""
        if "app.py" not in saw or "LITERAL" in saw:
            failures.append(f"the user's hook didn't see its glob match, or saw GIT_LITERAL_PATHSPECS: {saw!r}")

        # 5 and 6. A confirmed hit, including in a non-ASCII path, commits and then pushes without a second ask;
        # a changed file needs a new confirmation.
        rcf = repo(t / "confirmflow")
        gh_remote(t, rcf, "confirmflow")
        write(rcf / "caf\u00e9/x.py", SECRET); write(rcf / "a.txt", "a\n")
        r = tool(rcf, "screen", "--new-repo", lst("cf", "caf\u00e9/x.py", "a.txt"))
        conf = LISTS / "cf-confirmed.txt"
        conf.write_text("".join(l + "\n" for l in r.stdout.splitlines() if l.startswith(("CONTENT_HIT ", "HISTORY_"))))
        if "caf\u00e9/x.py" not in conf.read_text():
            failures.append(f"screen didn't report the non-ASCII hit: {r.stdout!r}")
        write(rcf / "caf\u00e9/x.py", SECRET.replace("abc", "xyz"))
        expect("a confirmed file that changed needs a new confirmation",
               tool(rcf, "commit", lst("cf", "caf\u00e9/x.py", "a.txt"), msg("cf", "x\n"), str(conf)), 2, ["SCREEN_FAILED"], ["COMMITTED"])
        write(rcf / "caf\u00e9/x.py", SECRET)
        expect("commit takes a confirmed non-ASCII hit",
               tool(rcf, "commit", lst("cf", "caf\u00e9/x.py", "a.txt"), msg("cf", "x\n"), str(conf)), 0, ["COMMITTED"])
        expect("push accepts the hit confirmed before the commit", tool(rcf, "push", "main", str(conf)), 0, ["PUSHED main"])

        # Push says why its own screen failed.
        rnf = repo(t / "netfail")
        sh(rnf, "git", "remote", "add", "origin", "ssh://git@github.com/does-not-exist.git")
        sh(rnf, "git", "commit", "-q", "--allow-empty", "-m", "1")
        expect("push reports a failed screen", tool(rnf, "push", "main"), 2, ["NET_FAILED"])

        # 7. A long history is screened in one pass, fast, and push reuses that screen.
        rl = repo(t / "long")
        stream = []
        for i in range(300):
            body = f"line {i}\n"
            stream.append(f"commit refs/heads/main\ncommitter t <t@example.invalid> {1700000000 + i} +0000\n"
                          f"data {len(f'c{i}')}\nc{i}\n"
                          f"M 100644 inline f{i % 20}.txt\ndata {len(body)}\n{body}\n")
        r = subprocess.run(["git", "fast-import", "--quiet"], cwd=rl, input="".join(stream), text=True,
                           capture_output=True, env=ENV)
        if r.returncode != 0:
            raise RuntimeError(f"fast-import: {r.stderr.strip()}")
        sh(rl, "git", "reset", "-q", "--hard", "main")
        write(rl / "late.py", SECRET); commit_all(rl, "late")
        start = time.monotonic()
        r = tool(rl, "screen", "--new-repo", lst("lg"))
        took = time.monotonic() - start
        expect("a 300-commit history is screened", r, 1, ["HISTORY_CONTENT_HIT", "late.py"])
        # gstack-timing-ok: a generous bound that only a per-commit or pickaxe-per-diff scan exceeds.
        if took > 20:
            failures.append(f"screening 300 commits took {took:.0f}s")
        expect("a history over the limit stops instead of hanging",
               tool(rl, "screen", "--new-repo", lst("lg2"), env={**ENV, "SHIPYARD_MAX_HISTORY": "100"}), 2, ["HISTORY_TOO_LONG"])

        # The GitHub guard runs for real: a local remote is refused.
        rg = repo(t / "guard")
        sh(t, "git", "init", "-q", "--bare", "-b", "main", "guard.git")
        sh(rg, "git", "remote", "add", "origin", "../guard.git")
        sh(rg, "git", "commit", "-q", "--allow-empty", "-m", "1")
        expect("push refuses a non-GitHub origin", tool(rg, "push", "main"), 2, ["isn't GitHub"], ["PUSHED"])
        if sh(t / "guard.git", "git", "rev-parse", "-q", "--verify", "main", check=False).returncode == 0:
            failures.append("push reached a non-GitHub remote")

    for f in failures:
        print(f"::error::{f}")
    return 1 if failures else 0


def test_shipyard_git():
    """The same checks, for pytest runners."""
    assert main() == 0, "\n".join(failures)


if __name__ == "__main__":
    sys.exit(main())
