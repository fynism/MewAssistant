"""Live SDK smoke: MCP_URL=https://... MCP_API_KEY=smk_... uv run python tests/m3_sdk_smoke.py"""

import asyncio
import os

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    url = os.environ["MCP_URL"]
    key = os.environ["MCP_API_KEY"]
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(*streams) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert names == {"listKnowledges", "retrieve"}, names
                result = await session.call_tool("listKnowledges", {})
                assert not result.isError, result
                print("protocol:", initialized.protocolVersion)
                print("tools:", sorted(names))
                print("knowledge_count:", len(result.structuredContent["items"]))


if __name__ == "__main__":
    asyncio.run(main())
