"""
Entry point.

Usage:
  python main.py list
  python main.py backup [--container NAME]
  python main.py restore CONTAINER
  python main.py web
"""

import sys
import os

# Ensure project root is on the path when running directly
sys.path.insert(0, os.path.dirname(__file__))

from cli.cli import cli

if __name__ == "__main__":
    cli()
