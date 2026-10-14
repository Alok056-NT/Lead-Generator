"""Allow `python -m leadgen`."""

import sys

from leadgen.cli import main

if __name__ == "__main__":
    sys.exit(main())
