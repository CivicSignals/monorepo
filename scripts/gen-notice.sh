#!/usr/bin/env bash
# Regenerate the third-party attribution file NOTICE.md from the API's runtime
# dependency tree. The CI `license-check` job (TODO A3) runs this and fails if
# the committed NOTICE.md is stale or a disallowed license appears.
#
# Usage:
#   ./scripts/gen-notice.sh           # write NOTICE.md
#   ./scripts/gen-notice.sh --check   # diff against committed NOTICE.md, fail on drift
#
# Determinism notes:
#   * pip-licenses is pinned (PIP_LICENSES_VERSION) so table formatting is stable.
#   * `uv sync --no-dev` scopes the venv to runtime deps only (no ruff/mypy/pytest).
#   * `--no-version` keeps NOTICE.md churn-free across patch bumps; the dependency
#     *set* is what we attribute, not exact pins.
set -euo pipefail

# pip-licenses release used to render the table; bump intentionally.
PIP_LICENSES_VERSION="5.5.5"

# Licenses we will not ship. CivicSignals is AGPL-3.0-only, so permissive and
# weak-copyleft deps are fine; this denylist catches proprietary / unknown /
# source-unavailable terms that would block redistribution.
DENY_LICENSES="UNKNOWN;UNLICENSED;Proprietary;Commercial;SSPL;BUSL;Business Source License;Elastic License;Elastic-2.0;CC-BY-NC;Aladdin Free Public License"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="${REPO_ROOT}/apps/api"
NOTICE_FILE="${REPO_ROOT}/NOTICE.md"

MODE="write"
if [[ "${1:-}" == "--check" ]]; then
  MODE="check"
fi

cd "${API_DIR}"

# Scope the resolved environment to runtime dependencies only.
uv sync --no-dev --frozen >/dev/null

run_pip_licenses() {
  # --from=mixed reads both Core-Metadata License fields and Trove classifiers,
  # which resolves SPDX expressions that the bare metadata field leaves UNKNOWN.
  uv run --no-sync --with "pip-licenses==${PIP_LICENSES_VERSION}" pip-licenses --from=mixed "$@"
}

# Gate: fail the build if any disallowed license is present.
run_pip_licenses \
  --ignore-packages civicsignals-api \
  --fail-on "${DENY_LICENSES}" >/dev/null

# Render the third-party attribution table (runtime deps, no version churn).
TABLE="$(run_pip_licenses \
  --ignore-packages civicsignals-api \
  --format=markdown --with-urls --no-version --order=name)"

GENERATED="$(cat <<EOF
# Third-Party Notices

CivicSignals is licensed under **AGPL-3.0-only**. This file lists the
third-party packages distributed with the backend API (\`apps/api\`) and their
licenses, for attribution.

Regenerate with \`./scripts/gen-notice.sh\`; CI (\`license-check\`, TODO A3)
fails if this file is stale or a disallowed license appears.

## Python (runtime dependencies)

${TABLE}
EOF
)"

if [[ "${MODE}" == "check" ]]; then
  if ! diff -u "${NOTICE_FILE}" <(printf '%s\n' "${GENERATED}") >/tmp/notice.diff 2>&1; then
    echo "NOTICE.md is out of date. Run ./scripts/gen-notice.sh and commit the result." >&2
    cat /tmp/notice.diff >&2
    exit 1
  fi
  echo "NOTICE.md is up to date."
else
  printf '%s\n' "${GENERATED}" > "${NOTICE_FILE}"
  echo "Wrote ${NOTICE_FILE}"
fi
