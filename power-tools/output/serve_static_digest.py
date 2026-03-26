#!/usr/bin/env python3
"""Serve the generated static digest site over HTTP."""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import OUTPUT_DIR


DEFAULT_BIND = os.environ.get("JUNKYARD_DIGEST_BIND", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("JUNKYARD_DIGEST_PORT", "8085"))
DEFAULT_DIRECTORY = Path(os.environ.get("JUNKYARD_DIGEST_DIR", str(OUTPUT_DIR / "static_digest_site")))


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default=DEFAULT_BIND, help="Bind address. Default: %(default)s")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port number. Default: %(default)s")
    parser.add_argument("--directory", default=str(DEFAULT_DIRECTORY), help="Directory to serve. Default: %(default)s")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    directory = Path(args.directory).resolve()
    if not directory.exists():
        raise SystemExit(f"Static digest directory does not exist: {directory}")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    with ThreadingHTTPServer((args.bind, args.port), handler) as httpd:
        print(f"Serving static digest from {directory} on http://{args.bind}:{args.port}/")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
