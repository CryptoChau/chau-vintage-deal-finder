#!/bin/sh
# Commit one state file and push it to the current branch only.
# A rejected push is retried onto that same remote branch.
# A non-main branch never fetches or resets the default branch and never
# pushes it. Only the branch in GITHUB_REF_NAME is updated.
set -eu

STATE_FILE="${1:?state file}"
COMMIT_MSG="${2:?commit message}"

BRANCH="${GITHUB_REF_NAME:-}"
if [ -z "$BRANCH" ]; then
  BRANCH=$(git rev-parse --abbrev-ref HEAD)
fi

if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
  echo "persist: refusing detached HEAD" >&2
  exit 1
fi

case "$BRANCH" in
  */*|*..*|-*|"")
    echo "persist: refusing unsafe branch name" >&2
    exit 1
    ;;
esac

if [ "$BRANCH" != "main" ]; then
  echo "persist: updating origin/${BRANCH} only" >&2
fi

SNAPSHOT="/tmp/state_fresh_persist.$$.json"
cp "$STATE_FILE" "$SNAPSHOT"
trap 'rm -f "$SNAPSHOT"' EXIT

commit_state() {
  git add -- "$STATE_FILE"
  if git diff --cached --quiet; then
    return 0
  fi
  git commit -m "$COMMIT_MSG"
}

commit_state

i=1
while [ "$i" -le 5 ]; do
  if git push origin "HEAD:${BRANCH}"; then
    exit 0
  fi
  echo "persist: push to origin/${BRANCH} rejected, retry ${i}/5" >&2
  git fetch origin "$BRANCH"
  git reset --hard "origin/${BRANCH}"
  cp "$SNAPSHOT" "$STATE_FILE"
  commit_state
  i=$((i + 1))
done

echo "persist: push to origin/${BRANCH} failed; the default branch was not modified" >&2
exit 1
