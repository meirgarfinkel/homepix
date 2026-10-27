"""Face detection (YuNet) and recognition (SFace)."""

import numpy as np
from PIL import Image

from ..config import FACE_SCORE, MIN_FACE_PX, WORK_SIZE
from ..util import normalize
from .models import ensure_models, model_path


class Faces:
    """Finds faces and turns each one into a 128-number 'who is this' vector."""

    def __init__(self):
        import cv2

        try:
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:
            pass
        ensure_models(["face_detect.onnx", "face_embed.onnx"])
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(str(model_path("face_detect.onnx")), "", (320, 320), FACE_SCORE, 0.3, 5000)
        self.rec = cv2.FaceRecognizerSF.create(str(model_path("face_embed.onnx")), "")

    def find(self, img: Image.Image):
        """Returns [(x, y, w, h, score, vector)] with the box as fractions of the photo size."""
        bgr = np.ascontiguousarray(np.asarray(img)[:, :, ::-1])
        h, w = bgr.shape[:2]
        self.det.setInputSize((w, h))
        _, found = self.det.detect(bgr)
        results = []
        if found is None:
            return results
        min_px = max(20, MIN_FACE_PX * max(w, h) / WORK_SIZE)  # scaled for small photos
        for row in found:
            fx, fy, fw, fh = (float(v) for v in row[:4])
            if min(fw, fh) < min_px:
                continue
            aligned = self.rec.alignCrop(bgr, row)
            vec = normalize(self.rec.feature(aligned)[0])
            results.append((fx / w, fy / h, fw / w, fh / h, float(row[-1]), vec))
        return results
