"""The AI models: downloaded once, then fully offline."""

import hashlib
import os
import urllib.request
from pathlib import Path

from ..config import data_dir
from ..util import is_tty, say

# (pinned to one revision each, so the files are always the ones these checksums are of)
HF = "https://huggingface.co/Xenova/mobileclip_s0/resolve/757d59c9c6870a76a4b0306f05f5061bca15c39f/"
GH = "https://media.githubusercontent.com/media/opencv/opencv_zoo/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/"
MODELS = {
    # local name: (url, size in bytes, sha256)
    "clip_vision.onnx": (
        HF + "onnx/vision_model.onnx",
        45_543_630,
        "17d3c037b1d488c10c50e09f6009ea5a198caef4e0e8f4ea5617b7cb2d067ac0",
    ),
    "clip_text.onnx": (
        HF + "onnx/text_model_int8.onnx",
        42_799_230,
        "fc8d87978623385c17a46331ffb9cb5ab7fe8b61c513c094602b85f08edd0a0b",
    ),
    "clip_tokenizer.json": (
        HF + "tokenizer.json",
        2_224_081,
        "72ed5c96db5729294468543e4bc75fce14ca63f58e37300290189ba1c1e52b85",
    ),
    "face_detect.onnx": (
        GH + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
        232_589,
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_embed.onnx": (
        GH + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
        38_696_353,
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}


class ModelDownloadError(RuntimeError):
    """A model could not be downloaded; the message says how to get it by hand."""


def model_path(name: str) -> Path:
    return data_dir() / "models" / name


def ensure_models(names):
    folder = data_dir() / "models"
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        url, size, sha = MODELS[name]
        dest = folder / name
        if dest.exists() and dest.stat().st_size == size:
            continue
        say(f"Downloading {name} ({size / 1e6:.0f} MB, one time only)...")
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "homepix/0.1"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
                got = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    if is_tty():
                        print(f"\r  {got / 1e6:6.1f} / {size / 1e6:.1f} MB", end="", flush=True)
            if is_tty():
                print()
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise ModelDownloadError(
                f"Could not download {name}: {e}\nYou can download it yourself from\n  {url}\nand save it as\n  {dest}"
            ) from e
        if tmp.stat().st_size != size:
            tmp.unlink(missing_ok=True)
            raise ModelDownloadError(f"Download of {name} was incomplete. Please run the command again.")
        if sha:
            digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
            if digest != sha:
                tmp.unlink(missing_ok=True)
                raise ModelDownloadError(f"Download of {name} failed its checksum. Please run the command again.")
        tmp.replace(dest)


def _ort_session(path: Path):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)  # leave half the CPU free
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


def _pick_output(session, wanted: str) -> str:
    names = [o.name for o in session.get_outputs()]
    for n in names:
        if wanted in n:
            return n
    return names[0]
