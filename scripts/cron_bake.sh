#!/usr/bin/env bash
# Cron-friendly bake wrapper. Sources .env, runs the offline bake tools that
# are safe to invoke idempotently. Designed to be scheduled hourly; every
# call either does a small amount of new ElevenLabs work (when quota is
# available and templates are missing) or exits as a no-op (cache hits +
# size-match skips). On EL quota exhaustion (HTTP 402) the underlying tool
# logs the error and moves on — the next cron tick picks back up.
#
# Add to your crontab (run `crontab -e`):
#     0 * * * * /home/mnky9800n/repos/rainy-city-radio/scripts/cron_bake.sh >> ~/rcr-bake.log 2>&1
#
# Tail the log:  tail -f ~/rcr-bake.log

set -euo pipefail

REPO=/home/mnky9800n/repos/rainy-city-radio
cd "$REPO"

# Required env vars (ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, NIM_API_KEY).
# .env is not in the repo — it lives per-host. Cron has a minimal env, so
# we have to load it explicitly.
if [[ ! -f .env ]]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] missing $REPO/.env — bake skipped" >&2
    exit 0
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

# uv may not be on PATH under cron's minimal environment. Try common locations.
UV=$(command -v uv || echo "")
if [[ -z "$UV" ]]; then
    for candidate in /home/mnky9800n/.local/bin/uv /usr/local/bin/uv /usr/bin/uv; do
        if [[ -x "$candidate" ]]; then
            UV="$candidate"
            break
        fi
    done
fi
if [[ -z "$UV" ]]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] uv not found on PATH or common locations" >&2
    exit 1
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] starting bake pass"

# generate_spots: idempotent re-runs are free if the static pool is already
# baked. Kept here so adding a new line to spots.py auto-bakes overnight.
"$UV" run python -m rcr.tools.generate_spots || true

# generate_intros: chips away at the per-track intro/outro catalog. The bulk
# of monthly EL quota goes here while the catalog is being built up.
"$UV" run python -m rcr.tools.generate_intros || true

# produce_commercials: voice-synth + bed-mix each commercial in the curated
# pool. Skip-if-fresh by default, so a fully-produced catalog is a free
# cache pass; otherwise chips at any unbaked commercial as EL quota allows.
# Silently no-ops if jennifer/commercial_beds/ is empty (need to run
# download_beds first).
"$UV" run python -m rcr.tools.produce_commercials || true

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] bake pass done"
