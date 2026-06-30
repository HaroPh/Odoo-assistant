"""Entry point for the ERP backend.

On Windows, psycopg3's async mode is incompatible with the ProactorEventLoop
("Psycopg cannot use the 'ProactorEventLoop'..."). uvicorn 0.46 hardcodes the
ProactorEventLoop for single-process Windows via its loop *factory* (it creates
the loop directly, ignoring the asyncio event-loop *policy*). So we cannot fix
this by setting a policy — we must drive uvicorn's ASGI server inside a
SelectorEventLoop we create ourselves.

Run:  python run.py     (from the backend/ directory)
"""
import asyncio
import os
import sys

from uvicorn import Config, Server


def main() -> None:
    config = Config(
        "src.main:app",
        host=os.environ.get("BACKEND_HOST", "0.0.0.0"),
        port=int(os.environ.get("BACKEND_PORT", "8000")),
    )
    server = Server(config)

    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    else:
        server.run()


if __name__ == "__main__":
    main()
