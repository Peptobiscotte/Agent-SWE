# pyright: reportPossiblyUnboundVariable = false
# pyright: reportCallIssue = false
# pyright: reportUnknownArgumentType = false
# pyright: reportUnknownVariableType = false

import asyncio
from contextlib import AsyncExitStack
from typing import Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool, CallToolResult
import shlex
import sys
import os


class MCPClient:
    def __init__(self, param: str | None, url: str | None) -> None:
        if param:
            mcp_params = shlex.split(param)
            self.params = StdioServerParameters(
                        command=mcp_params[0],
                        args=mcp_params[1:],
                        env=dict(os.environ)
                    )
        else:
            self.params = StdioServerParameters(
                command=sys.executable,
                args=["mcp_tools_mbpp.py"],
            )
        self.session: ClientSession | None = None
        self.exit_stack = AsyncExitStack()
        self.tools: list[Tool] = []
        self.url = url

    async def connect(self) -> None:
        if self.url:
            read, write, _ = await self.exit_stack.enter_async_context(
                streamable_http_client(self.url)
            )
        else:
            read, write = await self.exit_stack.enter_async_context(
                stdio_client(self.params)
            )
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await self.session.initialize()

    async def close(self) -> None:
        await self.exit_stack.aclose()

    async def load_tools(self) -> None:
        if self.session is None:
            raise RuntimeError("MCP client is not connected")
        tools = await self.session.list_tools()
        for tool in tools.tools:
            self.tools.append(tool)

    def create_manual(self) -> str:
        if not self.tools:
            raise RuntimeError("MCP Tools not loaded yet")
        tool_str_list: list[str] = []
        for tool in self.tools:
            cur_str = ""
            cur_str += f"tool name: {tool.name}\n"
            cur_str += f"tool description: {tool.description}\n"
            cur_str += f"tool schema: {tool.inputSchema}\n"
            tool_str_list.append(cur_str)
        res = "\n\n".join(tool_str_list)
        return res

    async def call_tool(
            self,
            name: str,
            args: dict[str, Any]
    ) -> CallToolResult:
        if self.session is None:
            raise RuntimeError("MCP client is not connected")
        return await self.session.call_tool(name, args)


async def main() -> None:
    client = MCPClient()
    await client.connect()
    await client.load_tools()
    print(await client.call_tool("add", {"a": 2, "b": 4}))
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
