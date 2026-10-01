#!/usr/bin/env python3
"""Container healthchecks for docker-compose.yml (stdlib only).

Exec-form `CMD ["python", "/app/scripts/healthcheck.py", "<mode>"]` keeps
every argv element free of spaces, so it survives engines that re-split a
compose healthcheck string on whitespace (rootless podman did, see
.agent/smoke-failure.md).

  api     GET http://127.0.0.1:8000/healthz must return 200.
  worker  the Postgres server named by DATABASE_URL must answer a protocol
          SSLRequest from this container ('S' or 'N'). Proves network
          reachability of a live Postgres server only: no authentication, no
          query, and not that the worker's poll loop is alive
          (scripts/smoke_compose.sh proves that).

Exit 0 healthy, 1 unhealthy, 2 usage error.
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 3
# Postgres SSLRequest: int32 length 8, int32 code 80877103.
_SSL_REQUEST = struct.pack("!ii", 8, 80877103)


def check_api() -> bool:
    with urllib.request.urlopen(
        "http://127.0.0.1:8000/healthz", timeout=TIMEOUT_SECONDS
    ) as response:
        return response.status == 200


def check_worker() -> bool:
    url = urllib.parse.urlsplit(os.environ["DATABASE_URL"])
    host, port = url.hostname or "localhost", url.port or 5432
    with socket.create_connection((host, port), timeout=TIMEOUT_SECONDS) as sock:
        sock.sendall(_SSL_REQUEST)
        return sock.recv(1) in (b"S", b"N")


MODES = {"api": check_api, "worker": check_worker}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in MODES:
        print(f"usage: healthcheck.py {{{'|'.join(MODES)}}}", file=sys.stderr)
        return 2
    try:
        healthy = MODES[argv[1]]()
    except Exception as exc:
        print(f"{argv[1]} unhealthy: {exc!r}", file=sys.stderr)
        return 1
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
