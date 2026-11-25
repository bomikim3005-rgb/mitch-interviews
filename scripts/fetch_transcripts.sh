#!/bin/zsh
# Job on a Mac, run four times a day by launchd (~/Library/LaunchAgents/com.mitch-interviews.transcripts.plist): fetch any
# missing transcripts for the word cloud and push them. GitHub Actions can't fetch
# them (YouTube's bot check), but a home connection usually can.
#
# Commits only transcripts/, so other work in progress is never committed.
set -euo pipefail
cd "${0:A:h}/.."
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="

git pull --rebase --autostash --quiet origin main     # the scheduled job's data commits
.venv/bin/python update.py --transcripts-only

git add transcripts
if git diff --cached --quiet -- transcripts; then
  echo "No new transcripts."
else
  git commit --quiet -m "Add transcripts (from Mac)" -- transcripts
fi

# Push whatever is waiting, including commits from an earlier run whose push failed
# (overnight, while the Mac is locked, git can't read the GitHub login from the keychain).
if [[ -n "$(git log --oneline origin/main..HEAD)" ]]; then
  git pull --rebase --autostash --quiet origin main   # in case the scheduled job pushed meanwhile
  git push --quiet origin main
  echo "Pushed: $(git log --oneline -1 --format=%s)."
fi
