# PyInstaller runtime hook, Mac only: load OpenCV's compiled module directly. In HomePix.app its
# Python part and its compiled part sit in different folders (Resources and Frameworks), which
# OpenCV's own loader can't cope with ("recursion is detected during loading of cv2").
import glob
import importlib.machinery
import importlib.util
import os
import sys

_here = os.path.dirname(sys._MEIPASS)  # HomePix.app/Contents
for _found in sorted(glob.glob(os.path.join(_here, "*", "cv2", "**", "cv2*.so"), recursive=True)):
    _spec = importlib.util.spec_from_file_location(
        "cv2", _found, loader=importlib.machinery.ExtensionFileLoader("cv2", _found)
    )
    _cv2 = importlib.util.module_from_spec(_spec)
    sys.modules["cv2"] = _cv2
    _spec.loader.exec_module(_cv2)
    break
