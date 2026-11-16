#!/usr/bin/env bash
# The git steps the Shipyard skill takes before a push, as one tested script,
# so the agent never pastes file paths or messages into shell commands.
#
#   bash shipyard-git.sh inventory                        files a commit could include
#   bash shipyard-git.sh destination                      where a push to origin goes (credentials hidden)
#   bash shipyard-git.sh screen [--new-repo] PATHS_FILE   check those files and the commits a push sends
#   bash shipyard-git.sh commit PATHS_FILE MESSAGE_FILE   commit exactly those paths, with that message
#   bash shipyard-git.sh fetch                            git fetch --prune origin, without prompting
#   bash shipyard-git.sh default-branch                   origin's default branch
#   bash shipyard-git.sh push BRANCH                      push that branch to origin, without prompting
#
# PATHS_FILE holds one path per line and MESSAGE_FILE the commit message. The
# agent writes both with its file-writing tool, never through a shell.
#
# Output lines start with an upper-case tag. Only file names, commit hashes and
# URLs with credentials hidden are printed, never file contents. Exit 0: clean
# or done. 1: hits to show the user. 2: stopped, and this step changed nothing
# (SCREEN_FAILED, PARENT_REPO, NO_IDENTITY, IN_PROGRESS, NO_ORIGIN, NET_FAILED,
# PUSH_REJECTED, PUSH_BLOCKED): tell the user.
#
# With GIT_SSH set (for example plink), ssh's BatchMode can't be added, so an
# ssh passphrase prompt can still wait for input.
set -u
export LC_ALL=C GIT_LITERAL_PATHSPECS=1 GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never
shopt -s nocasematch

# A secret-looking value, checked case-insensitively by grep -E, git log -G and [[ =~ ]].
P='PRIVATE KEY|(^|[^A-Za-z])(api[_-]?key|secret|token|pass(w(or)?d)?|pwd|private[_-]?key|credential|auth(orization)?|client-key-data)[A-Za-z0-9_]*[^A-Za-z0-9[:space:]]?[[:space:]]*[:=][[:space:]]*[^A-Za-z0-9[:space:]$]?[A-Za-z0-9_/+=@!#%^&*~-]{8,}([^A-Za-z0-9_/+=@!#%^&*~.(-]|$)|bearer[[:space:]]+[A-Za-z0-9._~+/-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{20,}|xox[abprs]-|[rs]k_live_|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{10,}\.|hooks\.slack\.com/services/|://[^/[:space:]:@]+:[^/[:space:]@${}]{8,}@'
# Names that are never committed, in any case (.env.example, .env.sample and .env.template are fine).
NEVER='(^|/)(\.env(\.[^/]*)?|[^/]*\.env|\.envrc|[^/]*\.(pem|key|p12|pfx|p8|jks|keystore|ppk|kdbx|sqlite|sqlite3|db|dump)|[^/]*\.tfstate([.][^/]*)?|[^/]*\.sql\.gz|id_(rsa|dsa|ecdsa|ed25519)|credentials\.json|[^/]*service[-_]?account[^/]*\.json|\.npmrc|\.pypirc|\.netrc|\.git-credentials|kubeconfig)$|(^|/)(\.terraform|node_modules|\.venv)/'
ENV_OK='(^|/)\.env\.(example|sample|template)$'
# Dependency folders: named once by inventory, never committed.
DEPS='^((.*/)?(node_modules|\.venv|venv|__pycache__|\.terraform|\.next)/)'
MAX_BYTES=52428800
CAP=200

TMP=$(mktemp -d 2>/dev/null) || { echo "SCREEN_FAILED cannot make a temp folder"; exit 2; }
cleanup() { [ -n "$TMP" ] && [ -d "$TMP" ] && rm -rf -- "$TMP"; }
trap cleanup EXIT

fail() { echo "SCREEN_FAILED $*"; exit 2; }
never() { [[ $1 =~ $NEVER ]] && ! [[ $1 =~ $ENV_OK ]]; }
bad_name() { [[ $1 == *[[:cntrl:]]* ]]; }
in_repo() { git rev-parse --git-dir >/dev/null 2>&1; }
has_commits() { git rev-parse -q --verify HEAD >/dev/null 2>&1; }
redact() { sed -E 's#(://)[^/@[:space:]]*@#\1***@#g'; }
err_line() { grep -E '^(fatal|error|remote|hint|!)' "$TMP/err" 2>/dev/null | head -n 1 | redact | cut -c1-200; }

top_level() {
  in_repo || return 0
  local top here
  top=$(cd "$(git rev-parse --show-toplevel)" 2>/dev/null && pwd -P) || fail "can't find the repo's top level"
  here=$(pwd -P)
  [ "$top" = "$here" ] || { echo "PARENT_REPO $top"; exit 2; }
}

# Network git with no prompts, keeping the user's own SSH command.
net() {
  if [ -n "${GIT_SSH:-}" ]; then
    git "$@"
  else
    local base="${GIT_SSH_COMMAND:-$(git config --get core.sshCommand || echo ssh)}"
    GIT_SSH_COMMAND="$base -o BatchMode=yes" git "$@"
  fi
}
net_fail() {
  if grep -q 'Host key verification failed' "$TMP/err"; then echo "NET_FAILED host key: this machine hasn't connected to that host before"
  else echo "NET_FAILED the remote needs credentials, or can't be reached: $(err_line)"; fi
  exit 2
}
need_origin() {
  in_repo || { echo "NOT_A_REPO"; exit 2; }
  git remote get-url origin >/dev/null 2>&1 || { echo "NO_ORIGIN"; exit 2; }
}
is_github() { [[ $1 =~ ^(https?://([^/@]*@)?|ssh://([^/@]*@)?|[^/@:]*@)?github\.com[:/] ]]; }
guard_github() {
  [ -n "${SHIPYARD_GIT_ALLOW_ANY_REMOTE:-}" ] && return 0   # for the fixture tests only
  local u
  while IFS= read -r u; do
    is_github "$u" || fail "origin pushes to $(printf '%s\n' "$u" | redact), which isn't GitHub"
  done < <(git remote get-url --push --all origin)
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

# Print up to CAP lines of a file, then the total; the full list stays in OUT_DIR.
emit_capped() { # $1 file, $2 tag for the total
  local total; total=$(wc -l < "$1" | tr -d ' ')
  head -n "$CAP" "$1"
  if [ "$total" -gt "$CAP" ]; then
    cp "$1" "$OUT_DIR/$2.txt"; echo "$2 $total (all of them: $OUT_DIR/$2.txt)"
  fi
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
      echo "${BASH_REMATCH[1]}" >> "$TMP/deps"
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
  sort "$TMP/deps" | uniq -c | while read -r count dir; do echo "FOLDER $dir $count files"; done
}

destination() {
  need_origin
  local u
  while IFS= read -r u; do
    if is_github "$u"; then echo "PUSH_TO $(printf '%s\n' "$u" | redact)"
    else echo "NOT_GITHUB $(printf '%s\n' "$u" | redact)"; fi
  done < <(git remote get-url --push --all origin)
}

screen() {
  local new_repo=0
  if [ "${1:-}" = "--new-repo" ]; then new_repo=1; shift; fi
  top_level
  read_paths "${1:-}"
  local hits=0 p rc size
  : > "$TMP/filehits"
  while IFS= read -r -d '' p; do
    if never "$p"; then
      echo "NAME_HIT $p" >> "$TMP/filehits"
    elif [ ! -e "$p" ] && [ ! -L "$p" ]; then
      echo "DELETED $p"
    elif [ -d "$p" ]; then
      fail "$p is a folder: list its files"
    else
      size=$(wc -c < "$p" | tr -d ' ')
      if [ "$size" -gt "$MAX_BYTES" ]; then
        echo "TOO_BIG $p" >> "$TMP/filehits"
      else
        grep -qIE -i -e "$P" -- "$p"; rc=$?
        case "$rc" in
          0) echo "CONTENT_HIT $p" >> "$TMP/filehits" ;;
          1) if [ "$size" -gt 0 ] && ! grep -qI '' -- "$p"; then echo "BINARY_NOT_SCANNED $p"; fi ;;
          *) fail "couldn't read $p" ;;
        esac
      fi
    fi
  done < "$TMP/paths"
  emit_capped "$TMP/filehits" FILE_HITS_TOTAL
  hits=$(wc -l < "$TMP/filehits" | tr -d ' ')
  echo "FILES_CHECKED $N"

  if ! in_repo; then echo "NO_REPO"
  elif ! has_commits; then echo "NO_COMMITS"
  else
    : > "$TMP/exclude"
    if [ "$new_repo" = 1 ] || ! git remote get-url origin >/dev/null 2>&1; then
      echo "RANGE whole history"
    else
      local urls n_urls
      urls=$(git remote get-url --push --all origin) || fail "can't read origin's push URL"
      n_urls=$(printf '%s\n' "$urls" | wc -l | tr -d ' ')
      if [ "$n_urls" -gt 1 ]; then
        echo "RANGE whole history (origin pushes to more than one URL)"
      else
        net ls-remote "$urls" > "$TMP/remote" 2>"$TMP/err" || net_fail
        # Exclude only commits the destination has now, not stale local tracking refs.
        cut -f1 "$TMP/remote" | sort -u | git cat-file --batch-check='%(objectname) %(objecttype)' 2>/dev/null \
          | awk '$2 == "commit" { print "^" $1 }' > "$TMP/exclude"
        echo "RANGE commits the push destination doesn't have"
      fi
    fi
    local -a L=(git -c core.quotePath=false log --stdin HEAD)
    "${L[@]}" --format='%h%x09%s' < "$TMP/exclude" > "$TMP/commits" 2>/dev/null || fail "git log failed"
    local h s
    : > "$TMP/commitlines"
    while IFS=$'\t' read -r h s; do
      if [[ $s =~ $P ]]; then echo "COMMIT $h [subject hidden: it looks like a secret]"
      else echo "COMMIT $h $s"; fi
    done < "$TMP/commits" > "$TMP/commitlines"
    emit_capped "$TMP/commitlines" COMMITS_TOTAL
    echo "COMMITS_TOTAL $(wc -l < "$TMP/commits" | tr -d ' ')"
    "${L[@]}" -m --name-only --format='commit %h' < "$TMP/exclude" > "$TMP/names" 2>/dev/null || fail "git log failed"
    local c="" line
    : > "$TMP/seen"
    while IFS= read -r line; do
      case "$line" in
        "commit "*) c=${line#commit } ;;
        "") ;;
        \"*) echo "HISTORY_NAME_HIT $c $line" ;;
        *) if never "$line"; then echo "HISTORY_NAME_HIT $c $line"; fi ;;
      esac
    done < "$TMP/names" | awk '!seen[$3]++' > "$TMP/namehits"
    "${L[@]}" -m -E -i -G"$P" --name-only --format='commit %h' < "$TMP/exclude" > "$TMP/content" 2>/dev/null || fail "git log -G failed"
    c=""
    while IFS= read -r line; do
      case "$line" in
        "commit "*) c=${line#commit } ;;
        "") ;;
        *) echo "HISTORY_CONTENT_HIT $c $line" ;;
      esac
    done < "$TMP/content" | awk '!seen[$0]++' > "$TMP/contenthits"
    cat "$TMP/namehits" "$TMP/contenthits" > "$TMP/historyhits"
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
  git -c user.useConfigOnly=true var GIT_COMMITTER_IDENT >/dev/null 2>&1 || {
    echo "NO_IDENTITY git has no user.name and user.email: ask the user to set them"; exit 2; }
  local gd f
  gd=$(git rev-parse --git-dir)
  for f in MERGE_HEAD REBASE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply; do
    [ -e "$gd/$f" ] && { echo "IN_PROGRESS a ${f%%_*} is in progress: finish or abort it first"; exit 2; }
  done
  [ -z "$(git ls-files -u)" ] || { echo "IN_PROGRESS there are unmerged files"; exit 2; }
  read_paths "${1:-}"
  [ "$N" -gt 0 ] || fail "no paths given: nothing committed"
  local p
  while IFS= read -r -d '' p; do
    never "$p" && fail "$p is a never-commit file"
    [ -d "$p" ] && fail "$p is a folder: list its files"
  done < "$TMP/paths"
  local idx
  idx=$(git rev-parse --git-path index)
  [ -f "$idx" ] && cp -p "$idx" "$TMP/index.bak"
  restore() { if [ -f "$TMP/index.bak" ]; then cp -p "$TMP/index.bak" "$idx"; else rm -f -- "$idx"; fi; }
  git add --pathspec-from-file="$TMP/paths" --pathspec-file-nul 2>"$TMP/err" || { restore; fail "git add failed: $(err_line)"; }
  git commit -q -F "$2" --pathspec-from-file="$TMP/paths" --pathspec-file-nul 2>"$TMP/err" || {
    restore; fail "git commit failed: $(err_line)"; }
  echo "COMMITTED $(git rev-parse --short HEAD) $N paths"
}

push() {
  local b="${1:-}"
  need_origin
  git check-ref-format --branch "$b" >/dev/null 2>&1 || fail "not a branch name: $b"
  [ "$(git branch --show-current)" = "$b" ] || fail "HEAD isn't on $b"
  guard_github
  net push -u origin "refs/heads/$b:refs/heads/$b" 2>"$TMP/err" && { echo "PUSHED $b"; return; }
  if grep -qiE 'GH013|push protection|cannot contain secrets' "$TMP/err"; then
    echo "PUSH_BLOCKED GitHub found a secret in these commits and refused the push"; exit 2
  elif grep -q '\[remote rejected\]' "$TMP/err" || grep -qE '^remote: ' "$TMP/err" && ! grep -qE '\[rejected\]' "$TMP/err"; then
    echo "PUSH_BLOCKED the remote declined the push: $(err_line)"; exit 2
  elif grep -qE '\[rejected\].*(fetch first|non-fast-forward)' "$TMP/err"; then
    echo "PUSH_REJECTED $b: the remote has commits this branch doesn't"; exit 2
  elif grep -q 'hook' "$TMP/err"; then
    echo "HOOK_FAILED a local git hook stopped the push: $(err_line)"; exit 2
  fi
  net_fail
}

if in_repo; then OUT_DIR=$(git rev-parse --git-path shipyard); else OUT_DIR="${TMPDIR:-/tmp}/shipyard-$$"; fi
mkdir -p "$OUT_DIR" 2>/dev/null || OUT_DIR="$TMP"

case "${1:-}" in
  inventory) inventory ;;
  destination) destination ;;
  screen) shift; screen "$@" ;;
  commit) shift; commit "$@" ;;
  fetch) need_origin; net fetch --prune --quiet origin 2>"$TMP/err" || net_fail; echo "FETCHED" ;;
  default-branch)
    need_origin
    out=$(net ls-remote --symref origin HEAD 2>"$TMP/err") || net_fail
    ref=$(printf '%s\n' "$out" | sed -n 's#^ref: refs/heads/\([^[:space:]]*\)[[:space:]]*HEAD$#\1#p')
    if [ -n "$ref" ]; then echo "DEFAULT $ref"
    elif [ -z "$out" ]; then echo "EMPTY_REMOTE"
    else echo "UNKNOWN"; fi ;;
  push) shift; push "$@" ;;
  *) echo "usage: shipyard-git.sh inventory | destination | screen [--new-repo] PATHS_FILE | commit PATHS_FILE MESSAGE_FILE | fetch | default-branch | push BRANCH" >&2; exit 2 ;;
esac
