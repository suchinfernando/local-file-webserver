"""Enable ``python3 -m fileserver`` as an alternative to ``python3 serve.py``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())