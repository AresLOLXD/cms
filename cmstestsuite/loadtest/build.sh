#!/usr/bin/env bash

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2026 Ares Ulises Juárez Martínez <aresulises8@hotmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Build the CMS image of one load-test target from a git ref, with that
# ref's own Dockerfile, and record what was built.
#   ./build.sh fork beta
#   ./build.sh upstream cc9dfafb
#
# The ranking web server runs from this same image in both targets
# (cmsRankingWebServer is on its PATH), so the fork's docker/Dockerfile.ranking
# is not built here: it repeats the whole CMS install for an image that
# the compose file does not use.
set -euo pipefail
TARGET=${1:?target: fork or upstream} REF=${2:?git ref}
[[ $TARGET == fork || $TARGET == upstream ]] || { echo "unknown target $TARGET" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
SHA=$(git -C "$REPO" rev-parse --verify "$REF^{commit}")
CTX=$(mktemp -d)
trap 'rm -rf "$CTX"' EXIT
git -C "$REPO" archive "$SHA" | tar -x -C "$CTX"
DC=(sg docker -c)
"${DC[@]}" "docker --context default build -t cmsload-$TARGET:latest '$CTX'"
mkdir -p "$HERE/out/images"
{
  echo "target=$TARGET ref=$REF sha=$SHA built=$(date -u +%FT%TZ)"
  "${DC[@]}" "docker --context default run --rm --entrypoint '' cmsload-$TARGET:latest sh -c 'g++ --version | head -1; python3 --version; javac -version 2>&1; isolate --version | head -1'"
} | tee "$HERE/out/images/$TARGET.txt"
