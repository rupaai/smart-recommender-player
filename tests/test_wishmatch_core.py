"""
Runs the Wishmatch package's own tests (packages/wishmatch/tests/test_core.py), so
`make check` covers them; the package keeps its tests inside it for when it is lifted out.

    uv run python tests/test_wishmatch_core.py
"""

import os
import runpy

runpy.run_path(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packages", "wishmatch", "tests",
                 "test_core.py"),
    run_name="__main__",
)
