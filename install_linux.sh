#!/usr/bin/env bash
# Installs Photo Finder for the current user and adds it to the applications menu.
# Run from the folder you downloaded:   bash install_linux.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BASE="${XDG_DATA_HOME:-$HOME/.local/share}/photofind"
APP="$BASE/app"
ENV="$BASE/env"
MENU="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

echo "Installing Photo Finder..."

if ! python3 -c "import ensurepip, venv" 2>/dev/null; then
  echo "Photo Finder needs python3-venv. You may be asked for your password."
  sudo apt-get install -y python3-venv
fi

mkdir -p "$APP" "$MENU"
rm -f "$APP/photofind.py" "$APP/photofind_ui.html"  # from before the code became a package
rm -rf "$APP/photofind"
cp -r "$SRC/photofind" "$APP/"
find "$APP/photofind" -name __pycache__ -prune -exec rm -rf {} +
for f in photofind_app.py photofind-icon.png requirements.txt; do
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
echo "Downloading the parts Photo Finder needs (one time, about 300 MB)..."
"$ENV/bin/python" -m pip install --quiet --upgrade pip
"$ENV/bin/python" -m pip install --quiet -r "$APP/requirements.txt"

cat > "$MENU/photofind.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Photo Finder
Comment=Browse, search and organize your photos
Exec="$ENV/bin/python" "$APP/photofind_app.py"
Icon=$APP/photofind-icon.png
Terminal=false
Categories=Graphics;Photography;
Keywords=photos;pictures;search;faces;
EOF
chmod +x "$MENU/photofind.desktop"
rm -f "$MENU/photofind-next.desktop"  # the preview entry, from while the app was being rebuilt
update-desktop-database "$MENU" >/dev/null 2>&1 || true

echo
echo "Done. Open Photo Finder from your applications menu."
