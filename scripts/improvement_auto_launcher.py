"""Improvement Auto - Windows launcher."""

import os
import sys
from pathlib import Path


def main():
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = (
            Path(__file__).resolve().parent.parent
            / "release"
            / "ImprovementAuto_0.2.0"
        )

    if not (
        base / "deploy" / "frontend_releases" / "active.json"
    ).is_file():
        raise RuntimeError("Frontend release not found")

    # Test build uses the clean production database.
    os.environ["DB_NAME"] = "cut_improvement_auto_prod"

    # MAX remains disabled until final activation.
    os.environ["CUT_MAX_ALL_CLIENTS_ENABLED"] = "0"

    os.chdir(base)
    sys.path.insert(0, str(base / "backend"))

    from app.main import app
    import uvicorn

    print("=== IMPROVEMENT AUTO 0.2.0 ===")
    print("DATABASE: cut_improvement_auto_prod")
    print("MAX AUTOMATIC DISPATCH: DISABLED")
    print("LOCAL URL: http://127.0.0.1:8002")

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8002,
        log_level="info",
        lifespan="off",
    )


if __name__ == "__main__":
    main()
