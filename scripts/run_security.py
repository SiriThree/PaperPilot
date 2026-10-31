"""Run the explicit security composition, optionally using reviewed A/C source checkouts."""

import argparse
from pathlib import Path
import sys
from threading import Thread
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def stop_when_requested(server, path):
    while not server.should_exit:
        if path.is_file():
            server.should_exit = True
            return
        time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--a-source", type=Path, help="Explicit reviewed repository checkout containing backend/app/data"
    )
    parser.add_argument(
        "--c-source", type=Path, help="Explicit reviewed repository checkout containing backend/app/ai"
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--stop-file", type=Path, help="Owned preview shutdown marker; allows lifespan cleanup on Windows"
    )
    options = parser.parse_args()
    if not 1 <= options.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    import app
    import uvicorn

    for source, package in ((options.a_source, "data"), (options.c_source, "ai")):
        if source is not None:
            path = source.resolve() / "backend/app"
            if not (path / package / "__init__.py").is_file():
                parser.error("The selected source does not contain the expected adapter package")
            app.__path__.append(str(path))
    server = uvicorn.Server(
        uvicorn.Config(
            "app.security.bootstrap:create_app",
            factory=True,
            host="127.0.0.1",
            port=options.port,
            access_log=False,
        )
    )
    if options.stop_file is not None:
        Thread(target=stop_when_requested, args=(server, options.stop_file), daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
