#!/usr/bin/env bash
# One-shot publish of NoHumanWrites from this Mac (run by Arnaud: `! bash ~/nohumanwrites/publish.sh [org]`).
# 1. secret scan  2. create the public repo (under $ORG if given, else the logged-in account)
# 3. push main + tag v0.1.0  4. GitHub release with the paper attached  5. open it in the browser.
set -euo pipefail
cd "$(dirname "$0")"
ORG="${1:-$(gh api user --jq .login)}"
REPO="$ORG/nohumanwrites"
echo "== secret scan"
if command -v gitleaks >/dev/null; then gitleaks detect --no-banner --redact --source . 2>&1 | tail -2; else echo "gitleaks not installed — grep fallback"; fi
if grep -rn -i "sk-ant-\|BEGIN OPENSSH PRIVATE" . --exclude-dir=.git --exclude-dir=eval --exclude=publish.sh -q; then echo "possible secret found — aborting"; exit 1; fi
echo "== repository $REPO"
if ! gh repo view "$REPO" >/dev/null 2>&1; then
  gh repo create "$REPO" --public --source=. --remote=origin \
    --description "Find the unattested spans in machine-written work: a signed write-time provenance ledger for code, prose, lyrics and sheet music. Paper + tool. Plus de Fun Agency." \
    --homepage "https://nohumanwrites.org"
else
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$REPO.git"
fi
git tag -a v0.1.0 -m "v0.1.0 — paper v0.2.1 + tool after two external reviews" 2>/dev/null || true
echo "== upload (local branch renamed to main if it is not already)"
[ "$(git branch --show-current)" = "main" ] || git branch -M main
git push -u origin main
git push origin v0.1.0
echo "== release"
gh release create v0.1.0 --repo "$REPO" --title "NoHumanWrites v0.1.0" \
  --notes-file /private/tmp/claude-501/-Users-arnaudchretien/4e966ea0-1228-4cff-95f9-46e5e3781623/scratchpad/release-notes.md \
  paper/nohumanwrites.html paper/nohumanwrites.md 2>/dev/null || echo "(release already exists or notes file gone — create it from the GitHub UI)"
gh repo edit "$REPO" --add-topic provenance --add-topic ai-safety --add-topic claude-code --add-topic authorship --add-topic c2pa >/dev/null 2>&1 || true
echo "== done: https://github.com/$REPO"
open "https://github.com/$REPO" 2>/dev/null || true
