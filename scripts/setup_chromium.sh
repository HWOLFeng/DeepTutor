#!/usr/bin/env bash
# Fetch one Chromium build for UI checks — once, outside git, outside the image.
#
# Why this exists: `npx playwright install chromium` downloads ~170 MB per
# container and the Playwright CDN is slow here. The container's layer is also
# thrown away whenever the image is rebuilt, so the download turns into a
# recurring tax. The browser is a *tool*, not application data: it lives under
# the already-mounted, git-ignored `data/` volume, so every container built
# from this compose file sees it at `/app/data/.tools/chromium/...` without a
# copy step.
#
# Usage:
#   scripts/setup_chromium.sh                     # download once, then verify
#   PLAYWRIGHT_CHROMIUM=... node probe.mjs        # use it in a script
#
# The system libraries Chromium needs (libatk, libnss3, …) are *not* part of
# the browser download; the script installs the missing ones as root when the
# launch check fails, which is the only step that needs the network after the
# first run.
set -euo pipefail

CONTAINER="${CONTAINER:-deeptutor}"
#: Pinned to the build the container's Playwright expects (web/package.json).
#: A newer Playwright still runs an older Chromium; it only warns.
CHROMIUM_BUILD="${CHROMIUM_BUILD:-1200}"
MIRROR="${MIRROR:-https://cdn.npmmirror.com/binaries/playwright}"
HOST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BROWSER="$HOST_ROOT/data/.tools/chromium/chrome-linux64/chrome"

run() { docker exec "$CONTAINER" sh -lc "$*"; }
run_root() { docker exec -u 0 "$CONTAINER" sh -lc "$*"; }

check_launch() {
  run "PLAYWRIGHT_CHROMIUM=/app/data/.tools/chromium/chrome-linux64/chrome \
       node -e \"require('/app/web/node_modules/playwright').chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM }).then(async b => { await b.close(); }).catch(() => process.exit(1))\"" >/dev/null 2>&1
}

if [ -x "$BROWSER" ]; then
  echo "chromium present: $BROWSER"
else
  echo "downloading chromium $CHROMIUM_BUILD from $MIRROR"
  mkdir -p "$HOST_ROOT/data/.tools"
  run "mkdir -p /app/data/.tools && cd /app/data/.tools \
       && curl -sS -L -o chromium-linux.zip '$MIRROR/builds/chromium/$CHROMIUM_BUILD/chromium-linux.zip' \
       && rm -rf chromium && mkdir -p chromium \
       && (unzip -q chromium-linux.zip -d chromium 2>/dev/null || python -c 'import zipfile; zipfile.ZipFile(\"chromium-linux.zip\").extractall(\"chromium\")') \
       && rm -f chromium-linux.zip \
       && chmod -R a+rX chromium \
       && chmod +x chromium/chrome-linux64/chrome chromium/chrome-linux64/chrome_crashpad_handler chromium/chrome-linux64/chrome_sandbox"
fi

if ! check_launch; then
  echo "chromium cannot start; installing its system libraries (needs root)"
  # apt's own sandbox user needs a world-writable /tmp; the image ships it 0700.
  run_root "chmod 1777 /tmp"
  run_root "cd /app/web && npx playwright install-deps chromium >/dev/null 2>&1 || true"
  check_launch || { echo "chromium still cannot start" >&2; exit 1; }
fi

echo "chromium ok: $BROWSER"
echo "use: PLAYWRIGHT_CHROMIUM=$BROWSER"
