#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${ALPHATRADE_PYTHON:-/home/ubuntu/miniconda3/envs/alpha311/bin/python}"
if [[ ! -x "$PYTHON" ]]; then echo 'Set ALPHATRADE_PYTHON to the isolated alpha311 interpreter.' >&2; exit 1; fi
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 X3S_USE_FILE_LOGGING=0 MPLBACKEND=Agg
unset X3S_TRADING_ACCOUNT ROBUST40_SERVER_WORKER ROBUST40_WARMUP_WORKER
# A release-local code cache is disposable and independent of trading snapshots.
export NUMBA_CACHE_DIR="${NUMBA_CACHE_DIR:-$(pwd)/.numba_cache/$(git rev-parse --short HEAD)}"
verify() {
  "$PYTHON" -X utf8 -B tools/deployment.py check
  producer_root="${ALPHA_PRODUCER_ROOT:-/home/ubuntu/alpha2}"
  "$PYTHON" -X utf8 -B -m pytest tests -q -p no:cacheprovider --producer-root "$producer_root"
}
case "${1:-status}" in
  status)
    git status --short
    git log -1 --format='%h %s'
    "$PYTHON" --version
    ;;
  check) "$PYTHON" -X utf8 -B tools/deployment.py check ;;
  verify) verify ;;
  update)
    "$PYTHON" -X utf8 -B tools/deployment.py stopped
    if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
      echo 'Tracked code has local changes; review and commit them before updating.' >&2; exit 1
    fi
    git fetch origin
    git switch main
    git merge --ff-only origin/main
    verify
    ;;
  *) echo 'Usage: bash tools/deploy_alphatrade.sh {status|check|verify|update}' >&2; exit 2 ;;
esac
