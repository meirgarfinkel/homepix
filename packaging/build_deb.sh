#!/usr/bin/env bash
# Builds HomePix.deb from the AppImage: the same app, as a package Ubuntu, Debian, Mint and Pop!_OS
# install with a double-click (it opens in their software installer). It goes in /opt/homepix, with
# HomePix in the applications menu and "Open With" for photos and videos.
#   bash packaging/build_deb.sh [HomePix-x86_64.AppImage] [HomePix.deb]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
APPIMAGE="$(readlink -f "${1:-$ROOT/HomePix-x86_64.AppImage}")"
OUT="$(readlink -f "${2:-$ROOT/HomePix.deb}")"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/homepix/__init__.py")"
# what HomePix needs from the system (every desktop has these already), as the AppImage's self-test checks
DEPENDS="libgl1, libegl1, libgbm1, libdrm2, libx11-6, libx11-xcb1, libxcb1, libfontconfig1, libfreetype6"
DEPENDS="$DEPENDS, libharfbuzz0b, libasound2t64 | libasound2"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Unpacking the AppImage..."
cp "$APPIMAGE" "$WORK/app.AppImage"
chmod +x "$WORK/app.AppImage"
(cd "$WORK" && ./app.AppImage --appimage-extract > /dev/null)

PKG="$WORK/pkg"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" \
  "$PKG/usr/share/icons/hicolor/512x512/apps" "$PKG/usr/share/icons/hicolor/scalable/apps"
cp -r "$WORK/squashfs-root/usr/lib/HomePix" "$PKG/opt/homepix"
cat > "$PKG/usr/bin/homepix" <<'EOF'
#!/bin/sh
exec /opt/homepix/HomePix "$@"
EOF
chmod 755 "$PKG/usr/bin/homepix"
cp "$ROOT/homepix-icon.png" "$PKG/usr/share/icons/hicolor/512x512/apps/homepix.png"
cp "$ROOT/homepix-icon.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/homepix.svg"
# the same entry the AppImage writes for itself (homepix/ui/app.py), pointing at the installed app
MIME="$(sed -n 's/^MimeType=//p' "$WORK/squashfs-root/homepix.desktop")"
cat > "$PKG/usr/share/applications/homepix.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=HomePix
Comment=Browse, search and organize your photos
Exec=homepix %F
Icon=homepix
Terminal=false
Categories=Graphics;Photography;Viewer;
Keywords=photos;pictures;search;faces;
MimeType=$MIME
EOF

cat > "$PKG/DEBIAN/control" <<EOF
Package: homepix
Version: $VERSION
Architecture: amd64
Maintainer: Meir Garfinkel <meirgarfinkel@users.noreply.github.com>
Installed-Size: $(du -sk "$PKG" | cut -f1)
Depends: $DEPENDS
Section: graphics
Priority: optional
Homepage: https://github.com/meirgarfinkel/homepix
Description: Private photo manager with AI search and face recognition
 Browse your photos by date and folder, search them by describing what's in
 them, and name the people in them. Everything runs on your own computer:
 nothing is uploaded.
EOF

chmod -R u+rwX,go+rX,go-w "$PKG"  # 755 for folders and programs, 644 for the rest
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT" > /dev/null
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
