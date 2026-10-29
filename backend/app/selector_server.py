"""Explicit development server entry point for psycopg async on Windows."""

import argparse
import asyncio
import sys

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = uvicorn.Config("app.dev_composition:create_app", factory=True, host=args.host, port=args.port)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
