"""The search model (MobileCLIP): photos and phrases become comparable vectors."""

import numpy as np
from PIL import Image

from ..config import CLIP_SIZE
from ..util import unit_vectors
from .models import _ort_session, _pick_output, ensure_models, model_path


class ClipImage:
    """Turns a photo into a 512-number 'meaning' vector."""

    def __init__(self):
        ensure_models(["clip_vision.onnx"])
        self.sess = _ort_session(model_path("clip_vision.onnx"))
        self.inp = self.sess.get_inputs()[0].name
        self.out = _pick_output(self.sess, "image_embeds")

    def embed(self, img: Image.Image) -> np.ndarray:
        w, h = img.size
        scale = CLIP_SIZE / min(w, h)
        img = img.resize((max(CLIP_SIZE, round(w * scale)), max(CLIP_SIZE, round(h * scale))), Image.BILINEAR)
        w, h = img.size
        left, top = (w - CLIP_SIZE) // 2, (h - CLIP_SIZE) // 2
        img = img.crop((left, top, left + CLIP_SIZE, top + CLIP_SIZE))
        x = np.asarray(img, dtype=np.float32) / 255.0  # MobileCLIP: no mean/std normalization
        x = x.transpose(2, 0, 1)[None]
        return unit_vectors(self.sess.run([self.out], {self.inp: x})[0][0])


class ClipText:
    """Turns a search phrase into the same kind of vector."""

    def __init__(self):
        from tokenizers import Tokenizer

        ensure_models(["clip_text.onnx", "clip_tokenizer.json"])
        self.tok = Tokenizer.from_file(str(model_path("clip_tokenizer.json")))
        self.tok.no_padding()
        self.tok.no_truncation()
        self.sess = _ort_session(model_path("clip_text.onnx"))
        self.inputs = self.sess.get_inputs()
        self.out = _pick_output(self.sess, "text_embeds")

    def embed(self, text: str) -> np.ndarray:
        ids = self.tok.encode(text).ids
        if len(ids) > 77:
            ids = ids[:76] + [ids[-1]]  # keep the end-of-text token
        mask = [1] * len(ids) + [0] * (77 - len(ids))
        ids = ids + [0] * (77 - len(ids))
        feed = {}
        for i in self.inputs:
            dtype = np.int32 if "int32" in i.type else np.int64
            if "mask" in i.name:
                feed[i.name] = np.array([mask], dtype=dtype)
            else:
                feed[i.name] = np.array([ids], dtype=dtype)
        return unit_vectors(self.sess.run([self.out], feed)[0][0])

    def query(self, text: str) -> np.ndarray:
        # Blend the plain phrase with "a photo of ..." - CLIP models search better that way.
        return unit_vectors(self.embed(text) + self.embed(f"a photo of {text}"))
