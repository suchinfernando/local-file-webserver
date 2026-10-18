#!/usr/bin/env python3
"""Command line entry point for local-file-webserver.

Examples:
    python3 serve.py
    python3 serve.py ~/Downloads --port 9000
    python3 serve.py ./public --password secret --read-only

Equivalent package invocation: ``python3 -m fileserver``.
"""

import sys

from fileserver.cli import main

if __name__ == "__main__":
    sys.exit(main())