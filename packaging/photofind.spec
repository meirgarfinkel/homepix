# PyInstaller recipe for Photo Finder (used by build_linux.sh and build_windows.bat).
import os
# Produces a folder dist/PhotoFinder/ containing the app and everything it needs,
# including its own copy of Python. The AI models are downloaded on first run.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")

datas = [
    (str(ROOT / "photofind" / "webapp" / "ui.html"), "photofind/webapp"),
    (str(ROOT / "photofind-icon.png"), "."),
]
hiddenimports = ["photofind.core", "photofind.webapp.server"]
if LINUX:
    hiddenimports += ["webview.platforms.qt", "qtpy", "PySide6.QtWebEngineWidgets",
                      "PySide6.QtWebEngineCore", "PySide6.QtWebChannel"]
if WINDOWS:  # the window uses Windows' built-in Edge engine through .NET
    hiddenimports += ["webview.platforms.edgechromium", "webview.platforms.winforms", "clr"]

# Things that get pulled in by accident and only add size.
excludes = [
    "tkinter", "matplotlib", "IPython", "pytest", "scipy", "pandas",
    "PyQt5", "PyQt6", "PySide2",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtQuick3D", "PySide6.QtDesigner", "PySide6.QtPdf",
    "PySide6.QtBluetooth", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
]
if not LINUX:
    excludes += ["qtpy", "PySide6", "webview.platforms.qt", "webview.platforms.gtk"]

a = Analysis(
    [str(ROOT / "photofind_app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
if LINUX:
    # PySide6 ships ~130 Qt libraries; the window only needs the ~30 that Qt's web engine
    # links against. Work out that list from the real dependencies (so it stays right when
    # Qt updates) and leave the rest out, along with translations and the QML folder.
    import re
    import subprocess
    import PySide6

    pyside = Path(PySide6.__file__).parent
    qtlib = pyside / "Qt" / "lib"
    # Plugins the window can use; others (QML tooling, printing, positioning) are left out.
    skip_plugins = {"qmltooling", "printsupport", "position", "sqldrivers", "multimedia", "sceneparsers",
                    "geometryloaders", "renderers", "renderplugins", "sensors", "texttospeech", "webview",
                    "designer", "assetimporters", "qmllint", "qmlls", "canbus", "geoservices"}

    def plugin_dir(dest):
        parts = dest.replace("\\", "/").split("/")
        return parts[3] if len(parts) > 4 and parts[:3] == ["PySide6", "Qt", "plugins"] else None

    a.binaries = [b for b in a.binaries if plugin_dir(b[0]) not in skip_plugins]
    seeds = [Path(src) for dest, src, _ in a.binaries
             if dest.replace("\\", "/").startswith(("PySide6/Qt/plugins/", "PySide6/Qt/libexec/"))
             or (dest.startswith("PySide6/") and dest.count("/") == 1 and ".so" in dest)]
    seeds += [pyside / "Qt" / "libexec" / "QtWebEngineProcess"]
    env = dict(os.environ, LD_LIBRARY_PATH=f"{qtlib}:{os.environ.get('LD_LIBRARY_PATH', '')}")
    needed, todo = set(), [s for s in seeds if s.exists()]
    while todo:
        out = subprocess.run(["ldd", str(todo.pop())], capture_output=True, text=True, env=env).stdout
        for name, path in re.findall(r"(\S+) => (\S+)", out):
            if name not in needed and path.startswith(str(qtlib)):
                needed.add(name)
                todo.append(Path(path))

    def keep(dest):
        dest = dest.replace("\\", "/")
        name = dest.rsplit("/", 1)[-1]
        if dest.startswith("PySide6/Qt/lib/libQt6") and name not in needed:
            return False
        if dest.startswith("PySide6/Qt/qml/"):
            return False
        if dest.startswith("PySide6/Qt/translations/"):
            return dest.endswith("qtwebengine_locales/en-US.pak")  # the engine's own UI text
        if name == "qtwebengine_devtools_resources.pak":
            return False
        return True

    before = len(a.binaries) + len(a.datas)
    a.binaries = [b for b in a.binaries if keep(b[0])]
    a.datas = [d for d in a.datas if keep(d[0])]
    print(f"photofind.spec: kept {len(needed)} Qt libraries, dropped {before - len(a.binaries) - len(a.datas)} files")

    # Libraries every Linux desktop has, which must come from that computer rather than the build
    # machine: graphics libraries match its drivers, and a newer system's drivers or fonts need its
    # own newer C++ runtime and font libraries. (A subset of the AppImage project's excludelist.)
    HOST_LIBS = {
        "libgcc_s.so.1", "libstdc++.so.6", "libmvec.so.1", "libz.so.1", "libexpat.so.1",
        "libGL.so.1", "libEGL.so.1", "libGLX.so.0", "libGLdispatch.so.0", "libOpenGL.so.0", "libglapi.so.0",
        "libgbm.so.1", "libdrm.so.2", "libX11.so.6", "libX11-xcb.so.1", "libxcb.so.1",
        "libfontconfig.so.1", "libfreetype.so.6", "libharfbuzz.so.0", "libfribidi.so.0", "libthai.so.0",
        "libasound.so.2",
    }
    a.binaries = [b for b in a.binaries if b[0].replace("\\", "/").rsplit("/", 1)[-1] not in HOST_LIBS]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoFinder",
    console=False,  # no terminal window
    icon=str(ROOT / "photofind-icon.ico") if WINDOWS else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PhotoFinder", upx=False)
