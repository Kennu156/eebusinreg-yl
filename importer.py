#!/usr/bin/env python3
"""
Estonian Business Registry Importer
Entry point — delegates to src/ modules.
"""
from src.cli import cli

if __name__ == "__main__":
    cli()