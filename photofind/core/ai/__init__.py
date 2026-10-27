"""The AI models: search (MobileCLIP) and faces (YuNet + SFace). Everything runs offline once downloaded."""

from .clip import ClipImage, ClipText
from .faces import Faces
from .models import ModelDownloadError, ensure_models, model_path

__all__ = ["ClipImage", "ClipText", "Faces", "ModelDownloadError", "ensure_models", "model_path"]
