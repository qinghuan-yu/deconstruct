import asyncio
import json
import os
from pathlib import Path


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class ModelSettings:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "model_settings.json"

    async def read_public(self):
        settings = await self.read_private()
        api_key = settings.get("api_key") or ""
        return {
            "configured": bool(api_key),
            "api_key_hint": self._mask(api_key),
            "base_url": settings.get("base_url") or DEFAULT_BASE_URL,
            "model": settings.get("model") or DEFAULT_MODEL,
        }

    async def read_private(self):
        return await asyncio.to_thread(self._read_private_sync)

    def _read_private_sync(self):
        settings = {
            "api_key": os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
            "base_url": os.getenv("MODEL_BASE_URL", DEFAULT_BASE_URL),
            "model": os.getenv("MODEL_NAME", DEFAULT_MODEL),
        }
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                saved = json.load(handle)
            settings.update({key: value for key, value in saved.items() if value is not None})
        return settings

    async def save(self, api_key, base_url, model):
        await asyncio.to_thread(self._save_sync, api_key, base_url, model)
        return await self.read_public()

    def _save_sync(self, api_key, base_url, model):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        current = self._read_private_sync()
        next_settings = {
            "api_key": api_key.strip() if api_key and api_key.strip() else current.get("api_key", ""),
            "base_url": (base_url or DEFAULT_BASE_URL).strip().rstrip("/"),
            "model": (model or DEFAULT_MODEL).strip(),
        }
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(next_settings, handle, ensure_ascii=False, indent=2)

    def _mask(self, api_key):
        if not api_key:
            return ""
        if len(api_key) <= 8:
            return "****"
        return f"{api_key[:4]}...{api_key[-4:]}"
