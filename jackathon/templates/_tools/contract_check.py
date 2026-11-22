"""Checks that the backend serves every endpoint in the API contract.

Run from the backend folder with the backend's own Python:
    python ../.jackathon/contract_check.py ../.jackathon/contract.json

It reads the app's OpenAPI schema, FastAPI's public list of every route.
(Walking `app.routes` breaks across FastAPI versions: newer ones keep
included routers as nested objects instead of flattening them.)
"""

import json
import re
import sys

sys.path.insert(0, ".")

from app.main import app  # noqa: E402


def normalize(path: str) -> str:
    # /api/items/{item_id} and /api/items/{id} are the same route.
    return re.sub(r"\{[^}]*\}", "{}", path.rstrip("/"))


paths = app.openapi().get("paths", {})
served = {(method.upper(), normalize(path)) for path, operations in paths.items() for method in operations}
with open(sys.argv[1], encoding="utf-8") as f:
    expected = json.load(f)

problems = []
missing = [f"{m} {p}" for m, p in expected if (m, normalize(p)) not in served]
if missing:
    problems.append("Endpoints in the contract that the backend does not serve:\n" + "\n".join(f"  {e}" for e in missing))
# Routes outside /api would hide the frontend's pages (e.g. /beds/1 returning JSON instead of the app).
outside = sorted(p for p in paths if not p.startswith("/api/"))
if outside:
    problems.append(
        "These routes are outside /api/. Every API route must start with /api/ (include routers with "
        "prefix='/api' exactly once):\n" + "\n".join(f"  {p}" for p in outside)
    )
if problems:
    print("\n\n".join(problems))
    sys.exit(1)
print(f"All {len(expected)} contract endpoints are served, all under /api/.")
