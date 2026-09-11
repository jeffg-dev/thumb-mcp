"""Actual stdio handshake, discovery and validation without touching a device."""
import asyncio
import os
import sys
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.parametrize('profile,count', [('core', 9), ('extended', 22)])
def test_stdio_profiles_and_invalid_call(profile, count):
    async def run():
        parameters = StdioServerParameters(
            command=sys.executable, args=['-m', 'thumb.server'],
            env={**os.environ, 'THUMB_TOOL_PROFILE': profile, 'THUMB_CONTROL_BANNER': '0'},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                assert len(result.tools) == count
                invalid = await session.call_tool('snapshot', {'response': 'invalid'})
                assert invalid.is_error
    asyncio.run(run())
