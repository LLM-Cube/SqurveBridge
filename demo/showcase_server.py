"""Serve the prebuilt showcase using Python only: python -m demo.showcase_server."""
from __future__ import annotations
import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7861)
    args = parser.parse_args()
    os.environ["SQURVE_DEMO_AGENT"] = "off"
    from demo.space_server import DIST_DIR, app
    if not (DIST_DIR / "index.html").is_file():
        parser.error("Build the frontend first with npm run build --prefix demo-app.")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
