"""
MCP Client for MyOldMachine.

Connects to external MCP servers and exposes their tools alongside the
built-in tool set. Servers are configured in mcp_servers.json.

Architecture:
  - On bot startup, connect to all configured MCP servers via stdio transport
  - Discover their tools via session.list_tools()
  - Merge into the unified tool schema (TOOL_DEFINITIONS)
  - Route MCP tool calls through session.call_tool()
  - Graceful shutdown on bot exit via AsyncExitStack

Requires: pip install "mcp[cli]"
"""

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Config file location
_CONFIG_FILE = Path(__file__).parent.parent / "mcp_servers.json"

# MCP SDK availability flag — set at import time
_MCP_AVAILABLE = False
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    _MCP_AVAILABLE = True
except ImportError:
    logger.info("MCP SDK not installed — MCP server support disabled. Install with: pip install 'mcp[cli]'")


@dataclass
class MCPTool:
    """A tool discovered from an MCP server."""
    name: str  # Name exposed to the LLM (may be prefixed)
    original_name: str  # Name as defined by the MCP server
    description: str
    input_schema: dict
    server_name: str


@dataclass
class MCPServerConnection:
    """A live connection to an MCP server."""
    name: str
    session: Any  # ClientSession
    tools: list["MCPTool"] = field(default_factory=list)


class MCPManager:
    """Manages connections to multiple MCP servers.

    Uses AsyncExitStack to keep stdio_client context managers alive
    for the bot's entire lifetime. Call disconnect_all() on shutdown.
    """

    def __init__(self):
        self._connections: dict[str, MCPServerConnection] = {}
        self._tool_index: dict[str, tuple[MCPServerConnection, MCPTool]] = {}
        self._exit_stack = AsyncExitStack()
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return _MCP_AVAILABLE

    @property
    def connected_servers(self) -> list[str]:
        return list(self._connections.keys())

    def get_tools(self) -> list[MCPTool]:
        """All tools from all connected MCP servers."""
        tools = []
        for conn in self._connections.values():
            tools.extend(conn.tools)
        return tools

    def get_tool_definitions(self) -> list[dict]:
        """MCP tools in unified schema format (matches TOOL_DEFINITIONS structure)."""
        definitions = []
        for tool in self.get_tools():
            definitions.append({
                "name": tool.name,
                "description": f"[MCP: {tool.server_name}] {tool.description}",
                "parameters": tool.input_schema,
            })
        return definitions

    def is_mcp_tool(self, name: str) -> bool:
        return name in self._tool_index

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call on the appropriate MCP server."""
        entry = self._tool_index.get(name)
        if not entry:
            return f"Error: MCP tool '{name}' not found"
        conn, tool = entry

        try:
            # Use original_name for the actual MCP call (name may be prefixed)
            result = await conn.session.call_tool(tool.original_name, arguments)
            parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif hasattr(block, "data"):
                    parts.append(f"[Binary data: {len(block.data)} bytes]")
                else:
                    parts.append(str(block))
            return "\n".join(parts) if parts else "Tool executed successfully (no output)."
        except Exception as e:
            logger.exception(f"MCP tool execution error: {name} on server {conn.name}")
            return f"Error executing MCP tool '{name}': {e}"

    async def connect_all(self) -> dict[str, str]:
        """
        Connect to all MCP servers defined in mcp_servers.json.
        Returns {server_name: status_message}.
        """
        if not _MCP_AVAILABLE:
            logger.info("MCP SDK not installed — skipping server connections")
            return {}

        config = _load_config()
        if not config:
            return {}

        results = {}
        for server_cfg in config:
            name = server_cfg.get("name", "unnamed")
            try:
                status = await asyncio.wait_for(
                    self._connect_server(server_cfg),
                    timeout=30.0,
                )
                results[name] = status
            except asyncio.TimeoutError:
                logger.error(f"MCP server '{name}' timed out during connection (30s)")
                results[name] = "Error: connection timed out"
            except Exception as e:
                logger.exception(f"Failed to connect MCP server: {name}")
                results[name] = f"Error: {e}"

        return results

    async def _connect_server(self, config: dict) -> str:
        """Connect to a single MCP server using AsyncExitStack."""
        name = config.get("name", "unnamed")
        command = config.get("command", "")
        args = config.get("args", [])
        env_overrides = config.get("env") or {}

        if not command:
            return f"Error: no command specified for server '{name}'"

        # Use sanitized environment (strips API keys, tokens, secrets)
        # MCP servers are external code and shouldn't inherit our credentials
        from core.tools import _build_command_env
        env = _build_command_env()
        env.update(env_overrides)

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
        )

        # Enter the stdio_client context manager via our exit stack —
        # this keeps the server subprocess alive until disconnect_all()
        streams = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        read_stream, write_stream = streams

        # Create and initialize the ClientSession
        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()

        # Discover tools — prefix names to avoid collisions with built-in tools
        # or tools from other MCP servers
        tools_response = await session.list_tools()
        builtin_names = {"run_command", "read_file", "write_file", "list_directory", "check_process"}
        tools = []
        for t in tools_response.tools:
            tool_name = t.name
            # Prefix with server name if it collides with built-in tools
            # or already exists in the tool index
            if tool_name in builtin_names or tool_name in self._tool_index:
                tool_name = f"{name}_{tool_name}"
            tool = MCPTool(
                name=tool_name,
                original_name=t.name,
                description=t.description or "",
                input_schema=t.inputSchema or {"type": "object", "properties": {}},
                server_name=name,
            )
            tools.append(tool)

        conn = MCPServerConnection(name=name, session=session, tools=tools)

        async with self._lock:
            self._connections[name] = conn
            for tool in tools:
                self._tool_index[tool.name] = (conn, tool)

        tool_names = [t.name for t in tools]
        logger.info(f"MCP server '{name}' connected: {len(tools)} tools ({tool_names})")
        return f"Connected — {len(tools)} tools: {', '.join(tool_names)}"

    async def disconnect_all(self):
        """Disconnect all MCP servers by closing the exit stack."""
        async with self._lock:
            self._connections.clear()
            self._tool_index.clear()
        try:
            await self._exit_stack.aclose()
        except Exception:
            logger.exception("Error closing MCP exit stack")
        # Reset for potential reconnection
        self._exit_stack = AsyncExitStack()
        logger.info("All MCP servers disconnected")


def _load_config() -> list[dict]:
    """Load MCP server configuration from mcp_servers.json."""
    if not _CONFIG_FILE.exists():
        return []

    try:
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            # Support {"servers": [...]} or {"mcpServers": {...}} formats
            if "servers" in data:
                return data["servers"]
            if "mcpServers" in data:
                servers = []
                for name, cfg in data["mcpServers"].items():
                    servers.append({"name": name, **cfg})
                return servers
            return []
        elif isinstance(data, list):
            return data
        else:
            logger.warning("Invalid mcp_servers.json format: expected list or dict")
            return []
    except Exception as e:
        logger.error(f"Failed to load mcp_servers.json: {e}")
        return []


# Singleton
_manager: Optional[MCPManager] = None


def get_mcp_manager() -> MCPManager:
    """Get or create the global MCP manager."""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager
