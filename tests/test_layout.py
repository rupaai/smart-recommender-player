"""
Offline checks of the code layout (tests/test_layout.py)

    uv run python tests/test_layout.py

The server image copies folders one by one (Dockerfile), so a new top-level
folder of code that the server imports must be copied too, or prod fails to start.
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


def top_level_imports(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


local = {name[:-3] if name.endswith(".py") else name for name in os.listdir(ROOT)
         if (name.endswith(".py") or os.path.isdir(os.path.join(ROOT, name, ""))) and not name.startswith(".")}
used = set()
for folder in ("", "web", "web/routes"):
    d = os.path.join(ROOT, folder)
    for name in os.listdir(d):
        if name.endswith(".py"):
            used |= top_level_imports(os.path.join(d, name)) & local
used.add("server")

docker = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
copied = set()
for line in re.findall(r"^COPY (.+)$", docker, flags=re.M):
    for part in line.split()[:-1]:
        copied.add(part.rstrip("/").removesuffix(".py"))

print("The server image has every local module the server imports")
for mod in sorted(used):
    check(f"Dockerfile copies {mod}", mod in copied, sorted(copied))

print("Routes live in web/routes, not in server.py")
server = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
check("server.py defines no API routes", not re.search(r"^@app\.(get|post|put|delete|options)\(", server, flags=re.M))

if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
print("\nall layout checks passed")
