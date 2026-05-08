# MCP Builder

Guide for building **Model Context Protocol** servers -- the standard way to expose tools, resources, and prompts to MCP-aware clients (Claude Desktop, Claude Code, Cursor, OpenAI Agents, custom hosts).

**When to load:** building or debugging an MCP server, or deciding whether a piece of work should ship as an MCP server vs. a normal API or skill.

---

## Decision: should this be an MCP server?

| Build MCP server when... | Don't when... |
|---|---|
| The capability needs to be reused across multiple LLM clients | A single bot uses it -- a normal Python skill is fine |
| You want hosts to discover tools dynamically | Tool surface is fixed and small |
| External users will run it against their own data | The capability is bound to your private infrastructure |
| You need typed, discoverable, schema-validated tool calls | A REST API is enough |

A bot skill (file in `skills/<name>/`) and an MCP server are not the same shape. Skills run inside one bot's process tree. MCP servers are standalone processes that any compliant client can attach to.

---

## Choosing language

- **Python** -- preferred for I/O-bound servers, integration with Python data libs, fastest path. SDK: `mcp` (PyPI), maintained by Anthropic.
- **TypeScript** -- preferred when targeting Cloudflare Workers / serverless, or when the host is a Node app. SDK: `@modelcontextprotocol/sdk`.
- **Go / Rust / others** -- viable, fewer first-party tools. Use the protocol directly via JSON-RPC over stdio.

Default to Python unless you have a specific reason.

---

## Three transports

| Transport | When | Trade-offs |
|---|---|---|
| **stdio** | Local servers launched by the host as a child process | Simplest, zero networking, only works locally |
| **streamable-HTTP** | Remote / network-accessible servers (current spec) | Bidirectional, supports notifications, the modern default |
| **SSE** | Older deployments | Deprecated -- supported by some hosts but not new ones; avoid for new builds |

**Default to stdio for development**, switch to streamable-HTTP only when remote access matters.

---

## Server skeleton (Python, stdio)

```python
# server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-server")

@mcp.tool()
def hello(name: str) -> str:
    """Say hello to someone.

    Args:
        name: The person's name.
    """
    return f"Hello, {name}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

Install: `pip install "mcp[cli]"` -- the `[cli]` extra brings `mcp dev` for inspecting your server with the MCP Inspector UI.

Run locally: `mcp dev server.py` opens an inspector in the browser, lets you call tools, list resources, and watch the JSON-RPC traffic.

---

## Three primitives

### 1. Tools -- functions the LLM can call

```python
@mcp.tool()
def calculate_total(items: list[float], tax_rate: float = 0.0) -> float:
    """Compute the total of a list of prices.

    Args:
        items: Item prices in your local currency.
        tax_rate: Tax fraction, e.g. 0.21 for 21%.
    """
    subtotal = sum(items)
    return round(subtotal * (1 + tax_rate), 2)
```

Type hints become the JSON Schema. Docstrings become the tool description shown to the LLM. Both are shipped to the host -- write them like prompts, not like internal API docs.

### 2. Resources -- read-only data the LLM can fetch

```python
@mcp.resource("project://state")
def project_state() -> str:
    """Current project state as JSON."""
    return Path("state.json").read_text()
```

Resources are addressable by URI. Prefer resources over tools for "give me X" reads -- hosts can cache and the user can pin them.

### 3. Prompts -- pre-baked prompt templates

```python
@mcp.prompt()
def code_review(code: str, language: str = "python") -> str:
    """Review code for issues."""
    return f"Review this {language} code for bugs and style issues:\n\n{code}"
```

Used by hosts to populate a slash menu of templates. Useful when you have a domain-specific workflow you want users to invoke without typing the full prompt.

---

## Streamable-HTTP server

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-server")
# tool / resource / prompt definitions...

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8765)
```

Add auth at this layer -- the SDK does not provide it, you do. Pass tokens through host headers; verify in middleware before the JSON-RPC handler runs.

---

## TypeScript skeleton

```typescript
// server.ts
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

const server = new Server({ name: "my-server", version: "0.1.0" }, { capabilities: { tools: {} } });

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [{
    name: "hello",
    description: "Say hello to someone.",
    inputSchema: {
      type: "object",
      properties: { name: { type: "string" } },
      required: ["name"],
    },
  }],
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name === "hello") {
    return { content: [{ type: "text", text: `Hello, ${req.params.arguments.name}` }] };
  }
  throw new Error(`Unknown tool: ${req.params.name}`);
});

await server.connect(new StdioServerTransport());
```

---

## Designing the tool surface

- **Few tools, broad arguments > many tools, narrow arguments.** A `query_db(sql)` tool is harder to misuse than 30 narrow `get_<entity>` tools, *if* the LLM has the context to write SQL safely. Pick based on caller skill.
- **Names are prompts.** `delete_record_permanently_no_undo` is better than `purge`.
- **Defaults matter.** A default that hides the dangerous case (`force=False`) means the LLM has to opt in to the unsafe path.
- **Return structured data when possible.** `{"items": [...], "next_cursor": "..."}` is more LLM-friendly than a stringified table.
- **Validate at the boundary.** The SDK validates JSON Schema; you validate semantic constraints inside the function.

---

## Error handling

```python
from mcp import McpError

@mcp.tool()
def get_user(user_id: int) -> dict:
    """Look up a user by ID."""
    user = db.query(...)
    if not user:
        raise McpError(code=404, message=f"No user with id {user_id}")
    return user.to_dict()
```

`McpError` flows back to the host as a structured error the LLM can reason about. A bare `raise ValueError(...)` works but is shown as "internal error" -- less useful.

---

## Testing

1. **Unit tests** for each tool function -- fastest feedback loop.
2. **`mcp dev server.py`** -- launches the Inspector UI for manual exploration.
3. **JSON-RPC fixture replay** -- record real client traffic, replay against new server versions to catch regressions.
4. **Integration with a real client** (Claude Desktop, Cursor) -- last gate before release.

---

## Common pitfalls

- **Stdout pollution kills stdio servers.** Anything printed to stdout that isn't valid JSON-RPC corrupts the stream. Use `stderr` for logs (`logging.basicConfig(stream=sys.stderr)`).
- **Long-running tools block the event loop.** Use `async def` tools for I/O; offload CPU work to a thread pool.
- **Client environment is not yours.** Don't rely on `os.environ` from your shell -- the host launches the server with its own env. Pass config via tool args or via an explicit init handshake.
- **Schema drift between SDK versions.** Pin the SDK version, regenerate client schemas after upgrades.
- **Tool names with spaces or punctuation** -- many hosts choke on them. Stick to `snake_case`.

---

## Distribution

| Audience | Distribute as |
|---|---|
| Internal team only | Git repo + run instructions |
| Wider Python audience | PyPI package + `mcp run my-server` |
| Wider Node audience | npm package |
| Claude Desktop users | Add to `claude_desktop_config.json` (they edit it manually) |
| Cursor users | Add to `~/.cursor/mcp.json` |

For Claude Desktop config, include a copy-paste block in your README:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "python",
      "args": ["-m", "my_server"]
    }
  }
}
```

---

## Reference

- [Spec home](https://modelcontextprotocol.io)
- [Spec repo](https://github.com/modelcontextprotocol/specification)
- [Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk)
- [Inspector UI](https://github.com/modelcontextprotocol/inspector)
- [Server registry / examples](https://github.com/modelcontextprotocol/servers)
