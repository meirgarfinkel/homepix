#!/usr/bin/env bash
# Builds PhotoFinder-x86_64.AppImage: one file people can download and double-click.
# Run from anywhere:   bash packaging/build_linux.sh
# The AppImage runs on the Linux it was built on and newer ones, so release builds are made on
# AlmaLinux 9 (glibc 2.34): run   bash packaging/build_in_container.sh   for that.
# Building here directly is quicker for trying things, but may not run on older systems.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
BUILD="${PHOTOFIND_BUILD:-$ROOT/build}"
ENV="$BUILD/env"
PYTHON="${PYTHON:-python3}"
ARCH="$(uname -m)"
# Pinned so builds are repeatable; update both lines together.
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64"
RUNTIME_SHA256="2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"

if [ "$ARCH" != "x86_64" ]; then
  echo "Only x86_64 builds are supported for now."
  exit 1
fi
if ! ldconfig -p 2>/dev/null | grep "libxcb-cursor.so.0" >/dev/null; then
  echo "The build needs one small system library first (it's bundled into the app). Install it:"
  echo "    Debian/Ubuntu:  sudo apt install libxcb-cursor0"
  echo "    Fedora/RHEL:    sudo dnf install xcb-util-cursor"
  echo "then run this script again."
  exit 1
fi

mkdir -p "$BUILD"
cd "$BUILD"

echo "1/4  Setting up a clean Python environment for building..."
# Made afresh when requirements.txt changes, so nothing left over from older versions gets bundled.
STAMP="$(sha256sum "$ROOT/requirements.txt" | cut -c1-16)"
if [ "$(cat "$ENV/.requirements" 2>/dev/null)" != "$STAMP" ]; then
  rm -rf "$ENV"
fi
if [ ! -x "$ENV/bin/python" ]; then
  "$PYTHON" -m venv "$ENV"
fi
"$ENV/bin/python" -m pip install --quiet --upgrade pip
"$ENV/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt" pyinstaller
echo "$STAMP" > "$ENV/.requirements"

echo "2/4  Bundling Photo Finder with PyInstaller..."
rm -rf "$BUILD/dist" "$BUILD/work"
"$ENV/bin/pyinstaller" --noconfirm --clean --log-level ERROR \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" "$HERE/photofind.spec"

echo "     Adding ExifTool with its own Perl..."
bash "$HERE/bundle_exiftool.sh" "$BUILD/exiftool" "$ENV/bin/python"
cp -r "$BUILD/exiftool" "$BUILD/dist/PhotoFinder/_internal/exiftool"

if [ -n "${GLIBC_MAX:-}" ]; then  # set by the release build: nothing may need a newer glibc than this
  need="$(find "$BUILD/dist/PhotoFinder" -type f -name "*.so*" -print0 | xargs -0 objdump -T 2>/dev/null \
          | grep -o "GLIBC_[0-9][0-9.]*" | sed "s/GLIBC_//" | sort -uV | tail -1)"
  if [ "$(printf '%s\n%s\n' "$need" "$GLIBC_MAX" | sort -V | tail -1)" != "$GLIBC_MAX" ]; then
    echo "A bundled library needs glibc $need, newer than $GLIBC_MAX. These ones:"
    find "$BUILD/dist/PhotoFinder" -type f -name "*.so*" -exec sh -c \
      'objdump -T "$1" 2>/dev/null | grep -q "GLIBC_$2" && echo "  $1"' _ {} "$need" \;
    exit 1
  fi
  echo "     glibc needed: $need (limit $GLIBC_MAX)"
fi

echo "3/4  Arranging the AppImage folder..."
APPDIR="$BUILD/PhotoFinder.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -r "$BUILD/dist/PhotoFinder" "$APPDIR/usr/lib/"
find "$APPDIR" -xtype l -delete  # links to Qt libraries the spec left out
cp "$ROOT/photofind-icon.png" "$APPDIR/photofind.png"
cat > "$APPDIR/photofind.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Photo Finder
Comment=Find your photos by describing them
Exec=PhotoFinder
Icon=photofind
Terminal=false
Categories=Graphics;Photography;
EOF
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/PhotoFinder/PhotoFinder" "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo "4/4  Packing it into a single AppImage file..."
fetch() {  # url sha256 dest: download once, check it, keep it
  if [ ! -f "$3" ]; then
    curl -fsSL -o "$3.part" "$1"
    echo "$2  $3.part" | sha256sum -c --quiet
    mv "$3.part" "$3"
  fi
}
TOOL="$BUILD/appimagetool-1.9.1-$ARCH.AppImage"
RUNTIME="$BUILD/runtime-20251108-$ARCH"
fetch "$APPIMAGETOOL_URL" "$APPIMAGETOOL_SHA256" "$TOOL"
fetch "$RUNTIME_URL" "$RUNTIME_SHA256" "$RUNTIME"
chmod +x "$TOOL"
OUT="${PHOTOFIND_OUT:-$ROOT}/PhotoFinder-$ARCH.AppImage"
# --appimage-extract-and-run lets the tool work even where FUSE isn't installed. It writes beside
# the old file and then swaps it in, which works even while the old one is running.
if ! ARCH="$ARCH" "$TOOL" --appimage-extract-and-run --no-appstream --runtime-file "$RUNTIME" "$APPDIR" \
    "$OUT.new" > "$BUILD/appimagetool.log" 2>&1; then
  cat "$BUILD/appimagetool.log"
  exit 1
fi
mv -f "$OUT.new" "$OUT"

echo
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
echo "Share that one file. People make it executable once (right-click > Properties > Allow"
echo "executing as program, or: chmod +x) and double-click it."
