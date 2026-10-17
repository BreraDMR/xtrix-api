"""Write the API contract to openapi.json (what the web client generates its types from).

python scripts/export_openapi.py          - rewrite openapi.json
python scripts/export_openapi.py --check  - fail if openapi.json is out of date (check.sh does this)
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# the schema doesn't depend on these, the app just refuses to import without them
os.environ.setdefault("DATABASE_URL", "postgresql://contract:contract@localhost/contract")
os.environ.setdefault("JWT_SECRET", "contract-export-only-not-a-real-secret-0000")

from app.main import app  # noqa: E402

TARGET = ROOT / "openapi.json"


def main():
    fresh = json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text() != fresh:
            sys.exit("openapi.json is out of date - run: python scripts/export_openapi.py")
        return
    TARGET.write_text(fresh)
    print(f"wrote {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
