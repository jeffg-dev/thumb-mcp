"""Read-only live benchmark. Run: uv run python scripts/benchmark.py."""
import asyncio
import json
import statistics
import time

from thumb import server


async def main():
    tools = await server.server.list_tools()
    catalog = json.dumps([t.model_dump(mode='json', by_alias=True, exclude_none=True)
                          for t in tools], separators=(',', ':'))
    durations = []
    lengths = []
    for _ in range(5):
        start = time.monotonic()
        result = await server.server.call_tool('snapshot', {})
        durations.append(time.monotonic() - start)
        lengths.append(sum(len(item.text) for item in result.content if item.type == 'text'))
        assert result.structured_content is None
        assert not any(item.type == 'image' for item in result.content)
    print(json.dumps({
        'tools': len(tools), 'tool_catalog_characters': len(catalog),
        'snapshot_seconds': [round(t, 3) for t in durations],
        'median_seconds': round(statistics.median(durations), 3),
        'snapshot_characters': lengths, 'ocr_calls': server.OBS.ocr_calls,
        'ocr_cache_hits': server.OBS.cache_hits,
        'note': 'Characters are not billed tokens. No screen text is logged.',
    }, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
