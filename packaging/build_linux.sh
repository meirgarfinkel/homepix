#!/usr/bin/env bash
# Builds PhotoFinder-x86_64.AppImage: one file people can download and double-click.
# Run from anywhere:   bash packaging/build_linux.sh
# Build on the OLDEST Linux you want to support; the AppImage runs on that and newer.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
BUILD="$ROOT/build"
ENV="$BUILD/env"
ARCH="$(uname -m)"

if ! ldconfig -p 2>/dev/null | grep "libxcb-cursor.so.0" >/dev/null; then
  echo "The build needs one small system library first. Run:"
  echo "    sudo apt install libxcb-cursor0"
  echo "then run this script again."
  exit 1
fi

mkdir -p "$BUILD"
cd "$BUILD"

echo "1/4  Setting up a clean Python environment for building..."
if [ ! -x "$ENV/bin/python" ]; then
  python3 -m venv "$ENV"
fi
"$ENV/bin/python" -m pip install --quiet --upgrade pip
"$ENV/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt" pyinstaller

echo "2/4  Bundling Photo Finder with PyInstaller..."
rm -rf "$BUILD/dist" "$BUILD/work"
"$ENV/bin/pyinstaller" --noconfirm --clean --log-level ERROR \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" "$HERE/photofind.spec"

echo "3/4  Arranging the AppImage folder..."
APPDIR="$BUILD/PhotoFinder.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -r "$BUILD/dist/PhotoFinder" "$APPDIR/usr/lib/"
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
TOOL="$BUILD/appimagetool-$ARCH.AppImage"
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
  chmod +x "$TOOL"
fi
OUT="$ROOT/PhotoFinder-$ARCH.AppImage"
# --appimage-extract-and-run lets the tool work even where FUSE isn't installed
ARCH="$ARCH" "$TOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$OUT" >/dev/null

echo
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
echo "Share that one file. People make it executable once (right-click > Properties > Allow"
echo "executing as program, or: chmod +x) and double-click it."
