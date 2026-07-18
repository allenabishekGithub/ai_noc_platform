#!/usr/bin/env python3
"""Compatibility wrapper for the modular TFS MCP server package."""

from tfs_mcp import TFSMCPServer, main, parse_args

__all__ = ["TFSMCPServer", "main", "parse_args"]


if __name__ == "__main__":
    main()
