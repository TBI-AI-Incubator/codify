"""The read tools over the library as an MCP server. `create_server` builds it;
`codify mcp` runs it."""

from codify.mcp.server import TOOL_NAMES, create_server

__all__ = ["TOOL_NAMES", "create_server"]
