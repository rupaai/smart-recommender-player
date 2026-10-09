"""Where the app finds its model files.

`MODELS_DIR` (env) points at the folder that holds `tiger/`, `vibe/` and
`model.onnx`. It defaults to `models/` next to the code, so nothing changes on
the Mac. On the server it points at `/models/current`.
"""

import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def models_dir() -> str:
    return os.environ.get("MODELS_DIR") or os.path.join(_PROJECT_ROOT, "models")
