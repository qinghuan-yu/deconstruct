"""显式运行才调用已配置的模型；只发送此文件内的合成测试问题。"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.chat_engine import ChatEngine
from backend.app.providers import OpenAICompatibleProvider
from backend.app.settings import ModelSettings
from backend.app.store import ConversationStore


async def main():
    settings = ModelSettings(ROOT / "data")
    public = await settings.read_public()
    if not public["configured"]:
        raise SystemExit("没有配置真实模型，未调用 API。")
    print(json.dumps({"model": public["model"], "real_api": True}), flush=True)
    store = ConversationStore(ROOT / ".qa" / "live.sqlite3")
    await store.init()
    engine = ChatEngine(store, OpenAICompatibleProvider(settings))
    conversation = await store.create_conversation("验收：本周阅读计划")
    results = []
    for mode, prompt in [
        ("expert", "我想用本周五天，每天 30 分钟，读完一本 180 页的非技术书并写一页读书笔记。我每天大约读 20 页。请判断是否可行，给出不增加每天时间的调整方案。不要引入付费工具。"),
        ("quick", "如果本周只有三天有空，应该怎么缩小目标？请用三条有序列表回答，保持每天 30 分钟。"),
    ]:
        started = time.monotonic()
        job = await engine.submit_user_message(conversation["id"], prompt, mode)
        cursor = 0
        first_delta = None
        delta_count = 0
        seen = set()
        while not job._task.done():
            for event in await store.list_events(conversation["id"], cursor):
                cursor = event["seq"]
                if event["created_at"] < time.time() - (time.monotonic() - started) - .1:
                    continue
                if event["type"] == "assistant_delta":
                    first_delta = first_delta or round(time.monotonic() - started, 2)
                    delta_count += 1
                if event["type"] == "debate_step_completed" and event["payload"]["key"] not in seen:
                    seen.add(event["payload"]["key"])
                    print(json.dumps({"stage": event["payload"]["key"], "seconds": event["payload"].get("elapsed_seconds")}), flush=True)
            await asyncio.sleep(.2)
        await job.wait()
        message = (await store.list_messages(conversation["id"]))[-1]
        result = {"mode": mode, "status": message["status"], "seconds": round(time.monotonic() - started, 2),
                  "first_delta_seconds": first_delta, "delta_events_observed": delta_count, "message": message}
        results.append(result)
        print(json.dumps({key: value for key, value in result.items() if key != "message"}), flush=True)
        if message["status"] != "complete":
            raise RuntimeError(message["error"])
    path = ROOT / ".qa" / "live-result.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    await engine.close()
    print("LIVE_SMOKE_PASS", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
