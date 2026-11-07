import unittest
from unittest.mock import patch

import httpx

from backend.app.providers import OpenAICompatibleProvider


class TestProvider(OpenAICompatibleProvider):
    async def _settings(self):
        return {"api_key": "test-only", "base_url": "https://example.test/v1", "model": "test"}


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def stream(self, lines):
        self.requests = []
        def handler(request):
            self.requests.append(request)
            return httpx.Response(200, text="\n\n".join("data: " + line for line in lines))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = TestProvider()
        with patch("httpx.AsyncClient", return_value=client):
            result = "".join([chunk async for chunk in provider.stream_reply([{"role": "user", "content": "test"}], "quick")])
        return result, provider.usage

    async def test_usage_only_event_and_v1_base_url(self):
        result, usage = await self.stream([
            '{"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}',
            '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
            '{"choices":[],"usage":{"total_tokens":12}}', '[DONE]',
        ])
        self.assertEqual(result, "hello")
        self.assertEqual(usage["total_tokens"], 12)
        self.assertEqual(str(self.requests[0].url), "https://example.test/v1/chat/completions")

    async def test_length_limited_reply_is_not_silently_complete(self):
        with self.assertRaisesRegex(ValueError, "长度"):
            await self.stream(['{"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}', '[DONE]'])

    async def test_broken_upstream_stream_is_not_silently_complete(self):
        with self.assertRaisesRegex(ValueError, "中断"):
            await self.stream(['{"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}'])
