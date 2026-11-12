"""HomePix: a private, local photo manager with AI search and face recognition."""

import os

# ONNX Runtime (the AI models) otherwise sends usage data to Microsoft from the moment it's loaded. Set
# before anything loads it; it's read only then.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

__version__ = "1.17.0"
