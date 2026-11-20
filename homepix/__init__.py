"""HomePix: a private, local photo manager with AI search and face recognition."""

import os

# ONNX Runtime (the AI models) otherwise sends usage data to Microsoft from the moment it's loaded. Set
# before anything loads it; it's read only then.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

# numpy's math library runs a thread on every core by default. For the small sums face naming does,
# that made each step about 150 times slower (half a second instead of 3 ms on an 8-core computer).
# A few threads are as fast as any. Set before numpy loads; it's read only then.
os.environ.setdefault("OPENBLAS_NUM_THREADS", str(min(4, os.cpu_count() or 1)))

__version__ = "1.17.2"
