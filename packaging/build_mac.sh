#!/usr/bin/env bash
# Builds HomePix.dmg: the disk image Mac users download, holding HomePix.app to drag to Applications.
# Run on a Mac, from anywhere:   bash packaging/build_mac.sh
# Needs Python 3.10-3.12 (python.org, or Homebrew's python@3.12). The app is made for the kind of Mac
# it's built on (Apple Silicon on an M1 or newer). It's signed only "ad hoc" (no Apple Developer
# account), so macOS asks people to allow it once: see the README.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
BUILD="${HOMEPIX_BUILD:-$ROOT/build}"
ENV="$BUILD/env"
PYTHON="${PYTHON:-python3}"

if [ "$(uname)" != "Darwin" ]; then
  echo "This builds the Mac app, so it runs on a Mac."
  exit 1
fi
mkdir -p "$BUILD"
cd "$BUILD"

echo "1/5  Setting up a clean Python environment for building..."
STAMP="$(shasum -a 256 "$ROOT/requirements.txt" | cut -c1-16)"
if [ "$(cat "$ENV/.requirements" 2>/dev/null)" != "$STAMP" ]; then
  rm -rf "$ENV"
fi
if [ ! -x "$ENV/bin/python" ]; then
  "$PYTHON" -m venv "$ENV"
fi
"$ENV/bin/python" -m pip install --quiet --upgrade pip
"$ENV/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt" pyinstaller
echo "$STAMP" > "$ENV/.requirements"

echo "2/5  Getting ExifTool (it runs on the Perl every Mac has)..."
# the same pinned version and checksum as the Linux build
VERSION="$(sed -n 's/^VERSION=\([0-9.]*\).*/\1/p' "$HERE/bundle_exiftool.sh")"
SHA256="$(sed -n 's/^SHA256=\([0-9a-f]*\).*/\1/p' "$HERE/bundle_exiftool.sh")"
TARBALL="$BUILD/downloads/Image-ExifTool-$VERSION.tar.gz"
mkdir -p "$BUILD/downloads"
if [ ! -f "$TARBALL" ] || ! echo "$SHA256  $TARBALL" | shasum -a 256 -c --quiet - 2>/dev/null; then
  curl -fsSL -o "$TARBALL.part" "https://sourceforge.net/projects/exiftool/files/Image-ExifTool-$VERSION.tar.gz/download"
  mv "$TARBALL.part" "$TARBALL"
fi
echo "$SHA256  $TARBALL" | shasum -a 256 -c --quiet - || { echo "ExifTool download doesn't match its checksum"; exit 1; }
rm -rf "$BUILD/exiftool" "$BUILD/exiftool-src"
mkdir -p "$BUILD/exiftool" "$BUILD/exiftool-src"
tar xzf "$TARBALL" -C "$BUILD/exiftool-src"
cp "$BUILD/exiftool-src/Image-ExifTool-$VERSION/exiftool" "$BUILD/exiftool/"
cp -R "$BUILD/exiftool-src/Image-ExifTool-$VERSION/lib" "$BUILD/exiftool/lib"
find "$BUILD/exiftool/lib" -name "*.pod" -delete
rm -rf "$BUILD/exiftool-src"
/usr/bin/perl "$BUILD/exiftool/exiftool" -ver >/dev/null || { echo "ExifTool doesn't run on this Mac's Perl"; exit 1; }

echo "3/5  Bundling HomePix with PyInstaller..."
rm -rf "$BUILD/dist" "$BUILD/work"
EXIFTOOL_BUNDLE="$BUILD/exiftool" "$ENV/bin/pyinstaller" --noconfirm --clean --log-level ERROR \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" "$HERE/homepix.spec"
APP="$BUILD/dist/HomePix.app"
# one consistent ad hoc signature over the whole app (Apple Silicon runs nothing unsigned)
codesign --force --deep --sign - "$APP"

echo "4/5  Checking that the bundled app starts..."
# (with a data folder of its own, so it never touches yours; AI models put in build/selftest/models
# are checked too)
mkdir -p "$BUILD/selftest"
rm -f "$BUILD/selftest/app.log"
if ! HOMEPIX_HOME="$BUILD/selftest" "$APP/Contents/MacOS/HomePix" --self-test; then
  cat "$BUILD/selftest/app.log" 2>/dev/null || true
  echo "OpenCV's files in the app:"; find "$APP" -path "*cv2*" -maxdepth 4 | head -20
  echo "The self-test failed: see above."
  exit 1
fi
cat "$BUILD/selftest/app.log" 2>/dev/null || true

echo "5/5  Making the disk image..."
STAGE="$BUILD/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/HomePix.app"
ln -s /Applications "$STAGE/Applications"  # so people can drag the app onto it
OUT="${HOMEPIX_OUT:-$ROOT}/HomePix.dmg"
rm -f "$OUT"
hdiutil create -quiet -volname HomePix -srcfolder "$STAGE" -ov -format UDZO "$OUT"
rm -rf "$STAGE"

echo
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
