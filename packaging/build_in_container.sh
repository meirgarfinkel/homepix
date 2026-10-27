#!/usr/bin/env bash
# Builds the release AppImage inside AlmaLinux 9 (glibc 2.34), so it runs on Debian 12+,
# Ubuntu 22.04+, Fedora, Arch and the like, whatever Linux this computer runs.
# Needs Docker or Podman.   Run:  bash packaging/build_in_container.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
ENGINE="${CONTAINER_ENGINE:-$(command -v podman || command -v docker || true)}"
if [ -z "$ENGINE" ]; then
  echo "This needs Docker or Podman. Install one, or run packaging/build_linux.sh directly."
  exit 1
fi
IMAGE=photofind-build:alma9

"$ENGINE" build -q -t "$IMAGE" -f "$HERE/containers/build.Containerfile" "$HERE/containers" >/dev/null
# Kept apart from build/ (a build made directly on this computer) since the two can't share a Python.
mkdir -p "$ROOT/build/alma9"
"$ENGINE" run --rm \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e PHOTOFIND_BUILD=/src/build/alma9 \
  -v "$ROOT:/src:z" -w /src \
  "$IMAGE" bash packaging/build_linux.sh | sed "s#/src/#$ROOT/#"
