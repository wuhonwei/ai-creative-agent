#!/usr/bin/env bash
# Check workspace, commit, and push to origin (hook + manual)
set -euo pipefail

cat >/dev/null

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  echo "[auto-push] Not a git repo, skip"
  exit 0
fi
cd "$ROOT"

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "[auto-push] No origin remote, skip"
  exit 0
fi

if git diff --quiet && git diff --cached --quiet; then
  UNTRACKED="$(git ls-files --others --exclude-standard)"
  if [[ -z "$UNTRACKED" ]]; then
    echo "[auto-push] No changes, skip"
    exit 0
  fi
fi

git add -A

git reset HEAD -- backend/.env 2>/dev/null || true
git reset HEAD -- .cursor/skills/.skills_store_lock.json 2>/dev/null || true
git reset HEAD -- backend/_chapters_list.txt 2>/dev/null || true
git reset HEAD -- backend/_humanize_batch.log 2>/dev/null || true
git reset HEAD -- backend/_humanize_progress.json 2>/dev/null || true
git reset HEAD -- backend/_list_chapters.py 2>/dev/null || true

if git diff --cached --quiet; then
  echo "[auto-push] No staged changes, skip"
  exit 0
fi

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
MSG="Auto-sync: agent code changes (${TS})"

if ! git commit -m "$MSG"; then
  echo "[auto-push] Commit failed or nothing to commit"
  exit 0
fi

BRANCH="$(git branch --show-current)"
echo "[auto-push] Pushing to origin/${BRANCH} ..."
if git push -u origin HEAD; then
  echo "[auto-push] Done: $(git log -1 --oneline)"
else
  echo "[auto-push] Push failed, check network or credentials" >&2
  exit 0
fi
