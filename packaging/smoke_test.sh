#!/usr/bin/env bash
# Runs the AppImage's self-test inside a fresh container of another Linux distribution, set up
# like a normal desktop (graphics, fonts, X libraries) but with nothing else installed.
#   bash packaging/smoke_test.sh debian:12 [PhotoFinder-x86_64.AppImage] [models folder]
# With a models folder (holding the downloaded AI models), it also checks the AI runs.
set -euo pipefail

IMAGE="$1"
APPIMAGE="$(readlink -f "${2:-PhotoFinder-x86_64.AppImage}")"
MODELS="${3:-}"
ENGINE="${CONTAINER_ENGINE:-$(command -v podman || command -v docker || true)}"

# What a desktop install of each family already has, plus a virtual screen (Xvfb).
DEB="xvfb xauth libgl1 libegl1 libgbm1 libdrm2 libx11-6 libx11-xcb1 libxcb1 libfontconfig1 libfreetype6"
DEB="$DEB libharfbuzz0b fonts-dejavu-core"
case "$IMAGE" in
  debian:*|ubuntu:*)  # the sound library was renamed in newer releases
    SETUP="export DEBIAN_FRONTEND=noninteractive; apt-get update -qq >/dev/null
      && { apt-get install -y -qq --no-install-recommends $DEB libasound2t64 >/dev/null 2>&1
           || apt-get install -y -qq --no-install-recommends $DEB libasound2 >/dev/null; }" ;;
  fedora:*)
    SETUP="dnf -y -q install --setopt=install_weak_deps=False xorg-x11-server-Xvfb xorg-x11-xauth mesa-libGL
      mesa-libEGL mesa-libgbm libdrm libX11 libX11-xcb libxcb fontconfig freetype harfbuzz alsa-lib
      dejavu-sans-fonts which util-linux shadow-utils >/dev/null" ;;
  archlinux:*)
    SETUP="pacman -Sy --noconfirm --needed -q xorg-server-xvfb xorg-xauth mesa libdrm libx11 libxcb fontconfig
      freetype2 harfbuzz alsa-lib ttf-dejavu which >/dev/null" ;;
  *) echo "No setup known for $IMAGE"; exit 1 ;;
esac
SETUP="$(echo "$SETUP" | tr -s ' \n' ' ')"

MOUNT_MODELS=()
if [ -n "$MODELS" ]; then
  MOUNT_MODELS=(-v "$(readlink -f "$MODELS"):/models:ro")
fi

# Runs as an ordinary user, the way people run it, from a copy (the AppImage extracts itself:
# containers have no FUSE).
"$ENGINE" run --rm -v "$APPIMAGE:/PhotoFinder.AppImage:ro" "${MOUNT_MODELS[@]}" "$IMAGE" bash -c "
  set -e
  $SETUP
  useradd -m tester
  su tester -c '
    set -e
    cd ~
    mkdir -p data/models
    [ -d /models ] && cp /models/* data/models/
    cp /PhotoFinder.AppImage . && chmod +x PhotoFinder.AppImage
    echo \"== $IMAGE (glibc \$(ldd --version | head -1 | grep -o \"[0-9.]*\$\"))\"
    PHOTOFIND_HOME=~/data APPIMAGE_EXTRACT_AND_RUN=1 timeout 180 \
      xvfb-run -a ./PhotoFinder.AppImage --self-test 2>&1 | tee log.txt | grep -E \"SELF-TEST|rror\" || true
    grep -q \"SELF-TEST OK\" log.txt || { echo \"--- full log\"; cat log.txt; exit 1; }
  '
"
