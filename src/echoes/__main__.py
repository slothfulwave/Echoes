"""Entry point for ``python -m echoes``."""

from __future__ import annotations

import sys

from echoes.cli import main

if __name__ == "__main__":
    sys.exit(main())
