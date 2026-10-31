#!/usr/bin/env python3
"""Export the authoritative DormPrint V1 OpenAPI document.

The FastAPI application builds its schema lazily through ``app.openapi()``.  In
FastAPI 0.162 ``include_router`` is lazy, so walking ``app.routes`` can miss
module routes; ``app.openapi()`` is the only complete source of truth.

This file intentionally only *exports* what the backend implements.  Path
prefixing, middleware documentation and frontend compatibility aliases belong
to ``scripts/generate-contract.mjs`` and ``scripts/extend-growth-contract.mjs``
so the authoritative document here can be diffed against the backend directly.
"""

from __future__ import annotations

import json
import sys

from app.main import create_app


def main() -> None:
    spec = create_app().openapi()
    # ``sort_keys`` keeps repeated exports byte-identical across processes.
    json.dump(
        spec,
        sys.stdout,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
