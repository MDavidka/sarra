#!/usr/bin/env bash
# One-shot upgrade when Settings → Update Syte cannot self-bootstrap.
# Usage: ./scripts/upgrade-from-pr.sh [PR_NUMBER]
# Use the highest open PR number (Settings → Update shows which PR).
set -euo pipefail

PR_NUMBER="${1:-14}"
SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SYTE_DIR"

WORK_BRANCH="syte-update"
LOCAL_REF="syte-pr-${PR_NUMBER}"

echo "==> Fetching PR #${PR_NUMBER}"
if ! git fetch origin "pull/${PR_NUMBER}/head:${LOCAL_REF}"; then
  echo "==> Refspec fetch failed, trying FETCH_HEAD"
  git fetch origin "pull/${PR_NUMBER}/head"
  git branch -f "${LOCAL_REF}" FETCH_HEAD
fi

echo "==> Checkout ${WORK_BRANCH} at ${LOCAL_REF}"
git checkout -B "${WORK_BRANCH}" "${LOCAL_REF}"

if [[ -x "${SYTE_DIR}/.venv/bin/pip" ]]; then
  echo "==> Installing Python dependencies"
  "${SYTE_DIR}/.venv/bin/pip" install -r "${SYTE_DIR}/requirements.txt" -q
fi

if command -v systemctl >/dev/null 2>&1; then
  echo "==> Restarting syte service"
  sudo systemctl restart syte || sudo systemctl restart syte.service
else
  echo "==> Restart Syte manually (systemctl not available)"
fi

echo "==> Done. Open Settings and confirm version updated."
