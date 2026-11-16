#!/usr/bin/env bash
# The git steps the Shipyard skill takes before a push, as one tested script,
# so the agent never pastes file paths or messages into shell commands.
#
#   bash shipyard-git.sh inventory                        files a commit could include
#   bash shipyard-git.sh remotes                          every remote, credentials hidden
#   bash shipyard-git.sh destination                      where a push to origin goes, credentials hidden
#   bash shipyard-git.sh screen [--new-repo] PATHS_FILE   check those files and the commits a push sends
#   bash shipyard-git.sh commit PATHS_FILE MESSAGE_FILE [CONFIRMED_FILE]
#                                                        commit exactly those paths, with that message
#   bash shipyard-git.sh fetch                            git fetch --prune origin, without prompting
#   bash shipyard-git.sh default-branch                   the push destination's default branch
#   bash shipyard-git.sh push BRANCH [CONFIRMED_FILE]     screen what the push sends, then push that branch
#
# PATHS_FILE holds one path per line, MESSAGE_FILE the commit message, and
# CONFIRMED_FILE the CONTENT_HIT and HISTORY_* lines the user said aren't secrets. The agent
# writes them with its file-writing tool, never through a shell.
#
# Output lines start with an upper-case tag. File contents are never printed;
# commit subjects are, unless they look like a secret, and URLs have their
# credentials hidden. Exit 0: clean or done. 1: hits to show the user.
# 2: stopped, and this step changed nothing (SCREEN_FAILED, PARENT_REPO,
# NOT_A_REPO, NO_ORIGIN, NO_IDENTITY, IN_PROGRESS, NET_FAILED, PUSH_REJECTED,
# PUSH_BLOCKED, HOOK_FAILED, INDEX_RESTORE_FAILED): tell the user.
#
# With GIT_SSH set (for example plink), ssh's BatchMode can't be added, so an
# ssh passphrase prompt can still wait for input.
set -u
export LC_ALL=C GIT_LITERAL_PATHSPECS=1 GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never GIT_NO_REPLACE_OBJECTS=1
export GIT_ASKPASS= SSH_ASKPASS=
shopt -s nocasematch

# A secret-looking value, checked case-insensitively by grep -E, git log -G/--grep and [[ =~ ]].
P='PRIVATE KEY|(^|[^A-Za-z])(api[_-]?key|secret|token|pass(w(or)?d)?|pwd|private[_-]?key|credential|auth(orization)?|client-key-data)[A-Za-z0-9_]*[^A-Za-z0-9[:space:]]?[[:space:]]*[:=][[:space:]]*[^A-Za-z0-9[:space:]$]?[A-Za-z0-9_/+=@!#%^&*~-]{8,}([^A-Za-z0-9_/+=@!#%^&*~.(-]|$)|bearer[[:space:]]+[A-Za-z0-9._~+/-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{20,}|xox[abprs]-|[rs]k_live_|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{10,}\.|hooks\.slack\.com/services/|://[^/[:space:]:@]+:[^/[:space:]@${}]{8,}@'
# Dependency folders: named once by inventory, never committed.
DEP_NAMES='node_modules|\.venv|venv|__pycache__|\.terraform|\.next'
DEPS="(^|/)($DEP_NAMES)/"
# Names that are never committed, in any case (.env.example, .env.sample and .env.template are fine).
NEVER="(^|/)(\.env(\.[^/]*)?|[^/]*\.env|\.envrc|[^/]*\.(pem|key|p12|pfx|p8|jks|keystore|ppk|kdbx|sqlite|sqlite3|db|dump)|[^/]*\.tfstate([.][^/]*)?|[^/]*\.sql\.gz|id_(rsa|dsa|ecdsa|ed25519)|credentials\.json|[^/]*service[-_]?account[^/]*\.json|\.npmrc|\.pypirc|\.netrc|\.git-credentials|kubeconfig)\$|$DEPS"
ENV_OK='(^|/)\.env\.(example|sample|template)$'
MAX_BYTES=52428800
CAP=200
OUT_DIR=""

TMP=$(mktemp -d 2>/dev/null) || { echo "SCREEN_FAILED cannot make a temp folder"; exit 2; }
cleanup() { [ -n "$TMP" ] && [ -d "$TMP" ] && rm -rf -- "$TMP"; }
trap cleanup EXIT

fail() { echo "SCREEN_FAILED $*"; exit 2; }
never() { [[ $1 =~ $NEVER ]] && ! [[ $1 =~ $ENV_OK ]]; }
bad_name() { [[ $1 == *[[:cntrl:]]* ]]; }
in_repo() { git rev-parse --git-dir >/dev/null 2>&1; }
has_commits() { git rev-parse -q --verify HEAD >/dev/null 2>&1; }
redact() { sed -E 's#(://)[^/@[:space:]]*@#\1***@#g'; }
# One line of git or hook output, hidden if it looks like a secret.
safe_line() { if [[ $1 =~ $P ]]; then echo "[details hidden: they look like a secret]"; else printf '%s\n' "$1" | redact | cut -c1-200; fi; }
err_line() {
  local l
  l=$(grep -E '^(fatal|error|remote|hint|!)' "$TMP/err" 2>/dev/null | head -n 1)
  [ -n "$l" ] || l=$(grep -m1 . "$TMP/err" 2>/dev/null)
  [ -n "$l" ] && safe_line "$l"
}

top_level() {
  in_repo || return 0
  local prefix
  prefix=$(git rev-parse --show-prefix 2>/dev/null) || fail "can't find the repo's top level"
  [ -z "$prefix" ] || { echo "PARENT_REPO $(git rev-parse --show-toplevel)"; exit 2; }
}

# Hosts: parse the real host out of a git URL, ignoring any user:password@.
url_host() {
  local u=$1 rest auth
  case "$u" in
    *://*) rest=${u#*://}; auth=${rest%%/*}; auth=${auth##*@}; auth=${auth%%:*}; printf '%s' "$auth" ;;
    /*|./*|../*|~*|[A-Za-z]:[\\/]*) ;;
    *:*) rest=${u%%:*}; printf '%s' "${rest##*@}" ;;
  esac
}
is_github() {
  local h real
  h=$(url_host "$1")
  [ -n "$h" ] || return 1
  case "$h" in github.com|ssh.github.com|www.github.com) return 0 ;; esac
  # An ssh alias (Host github-work, HostName github.com in ~/.ssh/config).
  if [[ $1 == ssh://* || $1 != *://* ]] && command -v ssh >/dev/null 2>&1; then
    real=$(ssh -G -- "$h" 2>/dev/null | awk '$1 == "hostname" { print $2; exit }')
    case "$real" in github.com|ssh.github.com) return 0 ;; esac
  fi
  return 1
}

# Network git with no prompts or askpass dialogs, keeping the user's own SSH command.
net() {
  if [ -n "${GIT_SSH:-}" ]; then
    git -c core.askPass= "$@"
  else
    local base="${GIT_SSH_COMMAND:-$(git config --get core.sshCommand || echo ssh)}"
    GIT_SSH_COMMAND="$base -o BatchMode=yes" git -c core.askPass= "$@"
  fi
}
net_fail() {
  if grep -q 'Host key verification failed' "$TMP/err"; then
    echo "NET_FAILED host key: this machine hasn't connected to that host before"
  elif grep -qiE 'could not resolve host|network is unreachable|operation not permitted|connection refused|timed out|no route to host' "$TMP/err"; then
    echo "NET_FAILED can't reach the remote: offline, or this client's sandbox blocks the network"
  elif grep -qiE 'src refspec .* does not match' "$TMP/err"; then
    echo "NET_FAILED the branch has no commits to push"
  else
    echo "NET_FAILED the remote needs credentials, or refused access: $(err_line)"
  fi
  exit 2
}
need_origin() {
  in_repo || { echo "NOT_A_REPO"; exit 2; }
  git remote get-url origin >/dev/null 2>&1 || { echo "NO_ORIGIN"; exit 2; }
}
# The one push URL for origin; fails with more than one, or one that looks like an option.
one_push_url() {
  local u n
  u=$(git remote get-url --push --all origin 2>/dev/null) || fail "can't read origin's push URL"
  n=$(printf '%s\n' "$u" | grep -c .)
  [ "$n" -eq 1 ] || return 1
  [[ $u == -* ]] && fail "origin's push URL starts with '-'"
  printf '%s\n' "$u"
}

out_dir() {
  [ -n "$OUT_DIR" ] && return 0
  if in_repo; then
    OUT_DIR=$(git rev-parse --git-path shipyard 2>/dev/null) && mkdir -p "$OUT_DIR" 2>/dev/null && [ -w "$OUT_DIR" ] \
      && OUT_DIR=$(cd "$OUT_DIR" && pwd -P) || OUT_DIR=""
  fi
  [ -n "$OUT_DIR" ] || OUT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/shipyard.XXXXXX" 2>/dev/null) || fail "can't save the full list anywhere"
}
# Print up to CAP lines of a file; past CAP, save the whole list and name it on a TAG line.
emit_capped() { # $1 file, $2 tag
  local total
  total=$(wc -l < "$1" | tr -d ' ')
  head -n "$CAP" "$1"
  if [ "$total" -gt "$CAP" ]; then
    out_dir
    cp "$1" "$OUT_DIR/$2.txt" || fail "can't save the full list"
    echo "$2 $total (all of them: $OUT_DIR/$2.txt)"
  fi
}

# Paths from a file (one per line) into a NUL-separated list; sets N.
read_paths() {
  [ -f "${1:-}" ] || fail "no path list file: ${1:-}"
  local p
  N=0
  : > "$TMP/paths"
  while IFS= read -r p || [ -n "$p" ]; do
    p=${p%$'\r'}
    [ -n "$p" ] || continue
    bad_name "$p" && fail "a path in the list has a control character"
    printf '%s\0' "$p" >> "$TMP/paths"
    N=$((N + 1))
  done < "$1"
}

# Read `git log -z --name-only --format=%x01%h` output; print "hash<TAB>path" per file.
log_names() {
  local tok c=""
  while IFS= read -r -d '' tok; do
    if [[ $tok == $'\x01'* ]]; then c=${tok#$'\x01'}; continue; fi
    tok=${tok#$'\n'}
    [ -n "$tok" ] && printf '%s\t%s\n' "$c" "$tok"
  done
}

inventory() {
  local -a G
  if in_repo; then
    top_level
    G=(git -c core.quotePath=false)
  else
    echo "NOT_A_REPO"
    git --git-dir="$TMP/scratch.git" --work-tree=. init -q 2>/dev/null || fail "git can't list this folder"
    G=(git -c core.quotePath=false --git-dir="$TMP/scratch.git" --work-tree=.)
  fi
  "${G[@]}" status --porcelain -z --untracked-files=all > "$TMP/status" 2>/dev/null || fail "git status failed"
  find . -path ./.git -prune -o -type f -size +51200k -print 2>/dev/null | sed 's#^\./##' > "$TMP/big"
  local big=$'\n'"$(cat "$TMP/big")"$'\n'
  : > "$TMP/deps"; : > "$TMP/out"
  local rec xy path from
  while IFS= read -r -d '' rec; do
    xy=${rec:0:2}; path=${rec:3}
    if [[ $xy == R* || $xy == C* ]]; then
      IFS= read -r -d '' from || from=""
      [[ $xy == R* ]] && [ -n "$from" ] && echo "DELETED $from" >> "$TMP/out"
    fi
    if bad_name "$path"; then
      echo "BAD_NAME $(printf '%q' "$path")" >> "$TMP/out"
    elif [[ $path =~ $DEPS ]]; then
      printf '%s\n' "$path" >> "$TMP/deps"
    elif [[ $xy == DD || $xy == AU || $xy == UD || $xy == UA || $xy == DU || $xy == AA || $xy == UU ]]; then
      echo "UNMERGED $path" >> "$TMP/out"
    elif [[ $xy == *D* ]]; then
      echo "DELETED $path" >> "$TMP/out"
    elif never "$path"; then
      echo "NEVER $path" >> "$TMP/out"
    elif [[ $big == *$'\n'"$path"$'\n'* ]]; then
      echo "TOO_BIG $path" >> "$TMP/out"
    else
      echo "FILE $path" >> "$TMP/out"
    fi
  done < "$TMP/status"
  emit_capped "$TMP/out" INVENTORY_TOTAL
  # Each dependency folder once, at its top-most level.
  DEPS_RE="^($DEP_NAMES)\$" awk -F/ 'BEGIN { re = ENVIRON["DEPS_RE"] }
    { p = ""; for (i = 1; i < NF; i++) { p = p $i "/"; if (tolower($i) ~ re) { print p; next } } }' "$TMP/deps" \
    | sort | uniq -c | while read -r count dir; do echo "FOLDER $dir $count files"; done > "$TMP/folders"
  emit_capped "$TMP/folders" FOLDERS_TOTAL
}

remotes() {
  in_repo || { echo "NOT_A_REPO"; exit 2; }
  local n u
  while IFS= read -r n; do
    while IFS= read -r u; do
      if is_github "$u"; then echo "REMOTE $n GITHUB $(printf '%s\n' "$u" | redact)"
      else echo "REMOTE $n OTHER $(printf '%s\n' "$u" | redact)"; fi
    done < <(git remote get-url --push --all "$n" 2>/dev/null)
  done < <(git remote)
}

destination() {
  need_origin
  local u
  while IFS= read -r u; do
    if is_github "$u"; then echo "PUSH_TO $(printf '%s\n' "$u" | redact)"
    else echo "NOT_GITHUB $(printf '%s\n' "$u" | redact)"; fi
  done < <(git remote get-url --push --all origin)
}

# The commits a push to origin sends, and what in them looks like a secret.
# $1: "whole" for the whole history; otherwise only what the push URL lacks.
# Writes HISTORY_* hit lines to $TMP/historyhits and prints COMMIT lines.
history_screen() {
  : > "$TMP/exclude"
  local url
  if [ "$1" = whole ] || ! git remote get-url origin >/dev/null 2>&1; then
    echo "RANGE whole history"
  elif ! url=$(one_push_url); then
    echo "RANGE whole history (origin pushes to more than one URL)"
  else
    net ls-remote -- "$url" > "$TMP/remote" 2>"$TMP/err" || net_fail
    # Exclude only commits the destination has now, not stale local tracking refs.
    cut -f1 "$TMP/remote" | sort -u | git cat-file --batch-check='%(objectname) %(objecttype)' 2>/dev/null \
      | awk '$2 == "commit" { print "^" $1 }' > "$TMP/exclude"
    echo "RANGE commits the push destination doesn't have"
  fi
  local -a L=(git -c core.quotePath=false log --stdin HEAD)
  "${L[@]}" --format='%h%x09%s' < "$TMP/exclude" > "$TMP/commits" 2>/dev/null || fail "git log failed"
  local h s total
  while IFS=$'\t' read -r h s; do
    if [[ $s =~ $P ]]; then echo "COMMIT $h [subject hidden: it looks like a secret]"
    else echo "COMMIT $h $s"; fi
  done < "$TMP/commits" > "$TMP/commitlines"
  emit_capped "$TMP/commitlines" COMMITS_TOTAL
  total=$(wc -l < "$TMP/commits" | tr -d ' ')
  [ "$total" -gt "$CAP" ] || echo "COMMITS_TOTAL $total"
  # Commit messages, subject and body.
  "${L[@]}" -E -i --grep="$P" --format='HISTORY_MESSAGE_HIT %h' < "$TMP/exclude" > "$TMP/msghits" 2>/dev/null || fail "git log --grep failed"
  # Never-commit and unreadable file names in any commit, merges included.
  "${L[@]}" -m -z --name-only --format='%x01%h' < "$TMP/exclude" > "$TMP/names" 2>/dev/null || fail "git log failed"
  local c path
  log_names < "$TMP/names" | while IFS=$'\t' read -r c path; do
    if bad_name "$path" || never "$path"; then printf '%s\t%s\n' "$c" "$path"; fi
  done | awk -F'\t' '!seen[$2]++ { print "HISTORY_NAME_HIT " $1 " " $2 }' > "$TMP/namehits"
  # Secret-looking lines, in text and binary diffs alike, whatever .gitattributes says.
  "${L[@]}" -m --text --no-textconv -E -i -G"$P" -z --name-only --format='%x01%h' < "$TMP/exclude" > "$TMP/content" 2>/dev/null \
    || fail "git log -G failed"
  log_names < "$TMP/content" | awk -F'\t' '!seen[$0]++ { print "HISTORY_CONTENT_HIT " $1 " " $2 }' > "$TMP/contenthits"
  cat "$TMP/msghits" "$TMP/namehits" "$TMP/contenthits" > "$TMP/historyhits"
}

screen() {
  local mode=dest
  if [ "${1:-}" = "--new-repo" ]; then mode=whole; shift; fi
  top_level
  read_paths "${1:-}"
  : > "$TMP/filehits"; : > "$TMP/scan"
  local p link
  while IFS= read -r -d '' p; do
    if never "$p"; then
      echo "NAME_HIT $p" >> "$TMP/filehits"
    elif [ -L "$p" ]; then
      # Git stores a symlink as its target text; check that, never what it points to.
      link=$(readlink -- "$p") || fail "couldn't read the link $p"
      [[ $link =~ $P ]] && echo "CONTENT_HIT $p" >> "$TMP/filehits"
    elif [ ! -e "$p" ]; then
      echo "DELETED $p"
    elif [ -d "$p" ]; then
      fail "$p is a folder: list its files"
    elif [ ! -f "$p" ] || [ ! -r "$p" ]; then
      fail "$p isn't a readable regular file"
    else
      printf '%s\0' "$p" >> "$TMP/scan"
    fi
  done < "$TMP/paths"
  if [ -s "$TMP/scan" ]; then
    perl -0ne 'chomp; print "$_\0" if -s $_ > '"$MAX_BYTES" < "$TMP/scan" > "$TMP/big" || fail "couldn't size the files"
    perl -0ne 'chomp; print "$_\0" if -s $_ <= '"$MAX_BYTES" < "$TMP/scan" > "$TMP/small" || fail "couldn't size the files"
    while IFS= read -r -d '' p; do echo "TOO_BIG $p" >> "$TMP/filehits"; done < "$TMP/big"
    xargs -0 grep -lIE -i --null -e "$P" -- < "$TMP/small" > "$TMP/content" 2>"$TMP/err"
    [ -s "$TMP/err" ] && fail "couldn't read every file: $(err_line)"
    while IFS= read -r -d '' p; do echo "CONTENT_HIT $p" >> "$TMP/filehits"; done < "$TMP/content"
    # Binary files (perl's -B, the same idea as grep -I) can't be read for secrets: name them.
    perl -0ne 'chomp; print "$_\0" if -s $_ && -B $_' < "$TMP/small" > "$TMP/notext" || fail "couldn't check for binary files"
    while IFS= read -r -d '' p; do echo "BINARY_NOT_SCANNED $p"; done < "$TMP/notext"
  fi
  emit_capped "$TMP/filehits" FILE_HITS_TOTAL
  echo "FILES_CHECKED $N"
  local hits
  hits=$(wc -l < "$TMP/filehits" | tr -d ' ')

  if ! in_repo; then echo "NO_REPO"
  elif ! has_commits; then echo "NO_COMMITS"
  else
    history_screen "$mode"
    emit_capped "$TMP/historyhits" HISTORY_HITS_TOTAL
    hits=$((hits + $(wc -l < "$TMP/historyhits" | tr -d ' ')))
  fi

  if [ "$hits" -gt 0 ]; then echo "SCREEN_HITS $hits"; exit 1; fi
  echo "SCREEN_CLEAN"
}

commit() {
  in_repo || fail "not a git repo yet: run git init first"
  top_level
  [ -s "${2:-}" ] || fail "no commit message file, or it's empty"
  grep -qIE -i -e "$P" -- "$2" && fail "the commit message looks like it holds a secret: rewrite it"
  git -c user.useConfigOnly=true var GIT_COMMITTER_IDENT >/dev/null 2>&1 || {
    echo "NO_IDENTITY git has no user.name and user.email: ask the user to set them"; exit 2; }
  local gd f what
  gd=$(git rev-parse --git-dir)
  for f in MERGE_HEAD:merge REBASE_HEAD:rebase rebase-merge:rebase rebase-apply:rebase CHERRY_PICK_HEAD:cherry-pick REVERT_HEAD:revert; do
    what=${f#*:}
    [ -e "$gd/${f%%:*}" ] && { echo "IN_PROGRESS a $what is in progress: finish or abort it first"; exit 2; }
  done
  [ -z "$(git ls-files -u)" ] || { echo "IN_PROGRESS there are unmerged files"; exit 2; }
  read_paths "${1:-}"
  [ "$N" -gt 0 ] || fail "no paths given: nothing committed"
  local confirmed="${3:-}"
  if [ -n "$confirmed" ] && [ ! -f "$confirmed" ]; then fail "no confirmed-hits file: $confirmed"; fi
  : > "$TMP/ok"
  [ -n "$confirmed" ] && { grep -E '^CONTENT_HIT ' "$confirmed" | sed 's/^CONTENT_HIT //' > "$TMP/ok" || true; }
  local p
  : > "$TMP/scan"
  while IFS= read -r -d '' p; do
    never "$p" && fail "$p is a never-commit file"
    [ -d "$p" ] && [ ! -L "$p" ] && fail "$p is a folder: list its files"
    [ -f "$p" ] && [ ! -L "$p" ] && printf '%s\0' "$p" >> "$TMP/scan"
  done < "$TMP/paths"
  # Re-screen the files as they are now; only hits the user confirmed may go in.
  if [ -s "$TMP/scan" ]; then
    xargs -0 grep -lIE -i -e "$P" -- < "$TMP/scan" 2>/dev/null | grep -vxF -f "$TMP/ok" > "$TMP/unconfirmed" || true
    [ -s "$TMP/unconfirmed" ] && fail "files look like they hold a secret and the user hasn't confirmed them: run screen again"
  fi

  local idx old
  idx=$(git rev-parse --git-path index)
  old=$(git rev-parse -q --verify HEAD || true)
  if [ -f "$idx" ]; then
    cp -p "$idx" "$TMP/index.bak" && cmp -s "$idx" "$TMP/index.bak" || fail "couldn't back up the git index: nothing changed"
  fi
  restore() {
    if [ -f "$TMP/index.bak" ]; then
      if ! { cp -p "$TMP/index.bak" "$idx" && cmp -s "$TMP/index.bak" "$idx"; }; then
        out_dir; cp -p "$TMP/index.bak" "$OUT_DIR/index.bak"
        echo "INDEX_RESTORE_FAILED the git index couldn't be put back; a copy is at $OUT_DIR/index.bak"; exit 2
      fi
    else
      rm -f -- "$idx"
    fi
  }
  undo() { if [ -n "$old" ]; then git update-ref HEAD "$old"; else git update-ref -d HEAD; fi; restore; }

  git add --pathspec-from-file="$TMP/paths" --pathspec-file-nul >"$TMP/out" 2>"$TMP/err" || { restore; fail "git add failed: $(err_line)"; }
  git commit -q -F "$2" --pathspec-from-file="$TMP/paths" --pathspec-file-nul >"$TMP/out" 2>"$TMP/err" || {
    restore; fail "git commit failed: $(err_line)"; }

  # Check what actually landed: hooks can add files or change them.
  git diff-tree --no-commit-id --name-only -r -z --root HEAD > "$TMP/landed" 2>/dev/null || { undo; fail "couldn't read the new commit"; }
  tr '\0' '\n' < "$TMP/paths" | sort -u > "$TMP/listed"
  tr '\0' '\n' < "$TMP/landed" | sort -u > "$TMP/landed.txt"
  comm -13 "$TMP/listed" "$TMP/landed.txt" > "$TMP/extra"
  if [ -s "$TMP/extra" ]; then
    undo
    fail "a git hook added files you didn't list, so the commit was undone: $(head -n 5 "$TMP/extra" | while IFS= read -r p; do safe_line "$p"; done | tr '\n' ' ')"
  fi
  xargs -0 git grep -lIE -i -e "$P" HEAD -- < "$TMP/landed" 2>/dev/null | sed 's/^HEAD://' \
    | grep -vxF -f "$TMP/ok" > "$TMP/landedhits" || true
  if [ -s "$TMP/landedhits" ]; then
    undo
    fail "what landed in the commit looks like it holds a secret (a git hook may have changed it), so the commit was undone"
  fi
  echo "COMMITTED $(git rev-parse --short HEAD) $N paths"
}

push() {
  local b="${1:-}" confirmed="${2:-}" url
  need_origin
  git check-ref-format --branch "$b" >/dev/null 2>&1 || fail "not a branch name: $b"
  [ "$(git branch --show-current)" = "$b" ] || fail "HEAD isn't on $b"
  url=$(one_push_url) || fail "origin pushes to more than one URL: ask the user which one, and push to it alone"
  is_github "$url" || fail "origin pushes to $(printf '%s\n' "$url" | redact), which isn't GitHub"
  if [ -n "$confirmed" ] && [ ! -f "$confirmed" ]; then fail "no confirmed-hits file: $confirmed"; fi

  # Screen exactly what this push sends; hits the user confirmed aren't secrets don't stop it.
  history_screen dest > "$TMP/range" || exit 2
  cp "$TMP/historyhits" "$TMP/open"
  [ -n "$confirmed" ] && { grep -vxF -f "$confirmed" "$TMP/historyhits" > "$TMP/open" || true; }
  if [ -s "$TMP/open" ]; then
    grep -E '^RANGE' "$TMP/range"
    emit_capped "$TMP/open" HISTORY_HITS_TOTAL
    echo "SCREEN_HITS $(wc -l < "$TMP/open" | tr -d ' ') (nothing was pushed)"
    exit 1
  fi

  net push --no-follow-tags -u -- "$url" "refs/heads/$b:refs/heads/$b" >"$TMP/out" 2>"$TMP/err" && {
    git branch -q --set-upstream-to="origin/$b" "$b" 2>/dev/null; echo "PUSHED $b"; return; }
  local hooks
  hooks=$(git config --get core.hooksPath || git rev-parse --git-path hooks)
  if grep -qiE 'GH013|push protection|cannot contain secrets' "$TMP/err"; then
    echo "PUSH_BLOCKED GitHub found a secret in these commits and refused the push"; exit 2
  elif grep -q '\[remote rejected\]' "$TMP/err"; then
    echo "PUSH_BLOCKED the remote declined the push: $(err_line)"; exit 2
  elif grep -qE '\[rejected\].*(fetch first|non-fast-forward)' "$TMP/err"; then
    echo "PUSH_REJECTED $b: the remote has commits this branch doesn't"; exit 2
  elif [ -x "$hooks/pre-push" ] && ! grep -qE '^remote: |\[rejected\]' "$TMP/err"; then
    echo "HOOK_FAILED the local pre-push hook stopped the push: $(err_line)"; exit 2
  fi
  net_fail
}

case "${1:-}" in
  inventory) inventory ;;
  remotes) remotes ;;
  destination) destination ;;
  screen) shift; screen "$@" ;;
  commit) shift; commit "$@" ;;
  fetch) need_origin; net fetch --prune --quiet origin >"$TMP/out" 2>"$TMP/err" || net_fail; echo "FETCHED" ;;
  default-branch)
    need_origin
    url=$(one_push_url) || { echo "UNKNOWN origin pushes to more than one URL"; exit 0; }
    out=$(net ls-remote --symref -- "$url" HEAD 2>"$TMP/err") || net_fail
    ref=$(printf '%s\n' "$out" | sed -n 's#^ref: refs/heads/\([^[:space:]]*\)[[:space:]]*HEAD$#\1#p')
    if [ -n "$ref" ]; then echo "DEFAULT $ref"
    elif [ -z "$out" ]; then echo "EMPTY_REMOTE"
    else echo "UNKNOWN"; fi ;;
  push) shift; push "$@" ;;
  *) echo "usage: shipyard-git.sh inventory | remotes | destination | screen [--new-repo] PATHS_FILE | commit PATHS_FILE MESSAGE_FILE [CONFIRMED_FILE] | fetch | default-branch | push BRANCH [CONFIRMED_FILE]" >&2; exit 2 ;;
esac
