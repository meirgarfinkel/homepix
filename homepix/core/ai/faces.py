"""Face detection (YuNet) and recognition (SFace)."""

import contextlib
from pathlib import Path

import numpy as np
from PIL import Image

from ..config import FACE_READ_MAX, FACE_READ_PX, FACE_SCORE, MIN_FACE_PX, WORK_SIZE
from ..media import load_photo
from ..util import unit_vectors
from .models import ensure_models, model_path


class Faces:
    """Finds faces and turns each one into a 128-number 'who is this' vector."""

    def __init__(self):
        import cv2

        with contextlib.suppress(Exception):
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        ensure_models(["face_detect.onnx", "face_embed.onnx"])
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(str(model_path("face_detect.onnx")), "", (320, 320), FACE_SCORE, 0.3, 5000)
        self.rec = cv2.FaceRecognizerSF.create(str(model_path("face_embed.onnx")), "")

    def find(self, img: Image.Image, path=None):
        """Returns [(x, y, w, h, score, vector, px)]: the box as fractions of the photo size, px the
        face's size in the pixels it was read from. With the photo's path, small faces are read from
        a bigger decode of it (the recognizer looks at 112 × 112: a face blown up to that is a blur)."""
        bgr = self._bgr(img)
        h, w = bgr.shape[:2]
        self.det.setInputSize((w, h))
        _, found = self.det.detect(bgr)
        if found is None:
            return []
        min_px = max(20, MIN_FACE_PX * max(w, h) / WORK_SIZE)  # scaled for small photos
        rows = [row for row in found if min(row[2], row[3]) >= min_px]
        big, k = self._bigger(path, (w, h), min((min(r[2], r[3]) for r in rows), default=None))
        results = []
        for row in rows:
            fx, fy, fw, fh = (float(v) for v in row[:4])
            src, scale = (big, k) if big is not None and min(fw, fh) < FACE_READ_PX else (bgr, 1.0)
            at = row.copy()
            at[:14] *= scale  # the box and the five landmarks
            aligned = self.rec.alignCrop(src, at)
            # the face and its mirror image, averaged: steadier than either alone
            vec = unit_vectors(self.rec.feature(aligned)[0] + self.rec.feature(self.cv2.flip(aligned, 1))[0])
            results.append((fx / w, fy / h, fw / w, fh / h, float(row[-1]), vec, min(fw, fh) * scale))
        return results

    @staticmethod
    def _bgr(img):
        return np.ascontiguousarray(np.asarray(img)[:, :, ::-1])

    def _bigger(self, path, size, smallest):
        """The photo decoded big enough for its smallest face to be FACE_READ_PX (as far as the
        photo has the pixels), and how much bigger than size it is; (None, 1) if not worth it."""
        if path is None or smallest is None or smallest >= FACE_READ_PX:
            return None, 1.0
        side = min(FACE_READ_MAX, int(max(size) * FACE_READ_PX / smallest) + 1)
        try:
            hi = load_photo(Path(path), side)
        except Exception:
            return None, 1.0
        if hi.width < size[0] * 1.15:  # the photo isn't much bigger than that
            return None, 1.0
        return self._bgr(hi), hi.width / size[0]
