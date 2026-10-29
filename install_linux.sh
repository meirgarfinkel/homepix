#!/usr/bin/env bash
# Installs HomePix for the current user and adds it to the applications menu.
# Run from the folder you downloaded:   bash install_linux.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BASE="${XDG_DATA_HOME:-$HOME/.local/share}/homepix"
OLD="${XDG_DATA_HOME:-$HOME/.local/share}/photofind"  # Photo Finder's, from before the new name
APP="$BASE/app"
ENV="$BASE/env"
MENU="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

echo "Installing HomePix..."

if ! python3 -c "import ensurepip, venv" 2>/dev/null; then
  echo "HomePix needs python3-venv. You may be asked for your password."
  sudo apt-get install -y python3-venv
fi

if [ -d "$OLD" ] && [ ! -e "$BASE" ]; then  # the library comes along: renamed, nothing copied
  if pgrep -u "$USER" -f "photofind_app.py|PhotoFinder" >/dev/null; then
    echo "Close Photo Finder first, then run this again."
    exit 1
  fi
  mv "$OLD" "$BASE"
  if [ -e "$BASE/photofind.db" ] && [ ! -e "$BASE/homepix.db" ]; then
    for x in "" -wal -shm; do
      if [ -e "$BASE/photofind.db$x" ]; then mv "$BASE/photofind.db$x" "$BASE/homepix.db$x"; fi
    done
  fi
  rm -rf "$BASE/app/photofind" "$BASE/app/photofind_app.py" "$BASE/app/photofind-icon.png"
  rm -rf "$BASE/env"  # (its scripts know the folder's old name: made again below)
fi
rm -f "$MENU/photofind.desktop"
mkdir -p "$APP" "$MENU"
rm -f "$APP/homepix.py" "$APP/homepix_ui.html"  # from before the code became a package
rm -rf "$APP/homepix"
cp -r "$SRC/homepix" "$APP/"
find "$APP/homepix" -name __pycache__ -prune -exec rm -rf {} +
for f in homepix_app.py homepix-icon.png requirements.txt; do
  cp "$SRC/$f" "$APP/"
done

# An environment from before the app went native still carries the old window's web engine and the
# rest of Qt (a few hundred MB more than the app needs): start that one afresh.
if [ -d "$ENV" ] && ls "$ENV"/lib/python3*/site-packages/webview >/dev/null 2>&1; then
  rm -rf "$ENV"
fi
if [ ! -x "$ENV/bin/python" ]; then
  python3 -m venv "$ENV"
fi
echo "Downloading the parts HomePix needs (one time, about 300 MB)..."
"$ENV/bin/python" -m pip install --quiet --upgrade pip
"$ENV/bin/python" -m pip install --quiet -r "$APP/requirements.txt"

cat > "$MENU/homepix.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=HomePix
Comment=Browse, search and organize your photos
Exec="$ENV/bin/python" "$APP/homepix_app.py"
Icon=$APP/homepix-icon.png
Terminal=false
Categories=Graphics;Photography;
Keywords=photos;pictures;search;faces;
EOF
chmod +x "$MENU/homepix.desktop"
rm -f "$MENU/homepix-next.desktop"  # the preview entry, from while the app was being rebuilt
update-desktop-database "$MENU" >/dev/null 2>&1 || true

echo
echo "Done. Open HomePix from your applications menu."
