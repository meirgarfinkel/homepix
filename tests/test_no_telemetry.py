"""Nothing about the library leaves the computer: ONNX Runtime (the AI models) would send usage data
to Microsoft from the moment it's loaded, so it's turned off before anything loads it."""

import os
import subprocess
import sys


def test_turned_off_before_onnx_runtime_loads():
    code = (
        "import homepix.core.ai.models, os, sys; "
        "print(os.environ.get('ORT_DISABLE_TELEMETRY'), 'onnxruntime' in sys.modules)"
    )
    env = {k: v for k, v in os.environ.items() if k != "ORT_DISABLE_TELEMETRY"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout
    assert out.split() == ["1", "False"]  # (set by importing HomePix, before ONNX Runtime is loaded)


def test_each_session_turns_it_off_too(monkeypatch, tmp_path):
    import onnxruntime as ort

    from homepix.core.ai import models

    calls = []
    monkeypatch.setattr(ort, "disable_telemetry_events", lambda: calls.append(1))
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: "session")
    assert models._ort_session(tmp_path / "m.onnx") == "session"
    assert calls == [1]
