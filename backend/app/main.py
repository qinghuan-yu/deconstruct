import asyncio
from contextlib import asynccontextmanager
import json
import os
from urllib.parse import parse_qs
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .chat_engine import ChatEngine
from .document_parser import DocumentParseError, MAX_UPLOAD_BYTES, extract_document_text
from .providers import OpenAICompatibleProvider
from .settings import ModelSettings
from .store import ConversationStore


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
FRONTEND_DIR = BASE_DIR / "frontend"

store = ConversationStore(DATA_DIR / "chat.sqlite3")
model_settings = ModelSettings(DATA_DIR)
engine = ChatEngine(store, provider=OpenAICompatibleProvider(model_settings))


@asynccontextmanager
async def lifespan(app):
    await store.init()
    await store.recover_interrupted()
    try:
        yield
    finally:
        await engine.close()


app = FastAPI(title="Decision Arena", lifespan=lifespan)


class CreateConversationRequest(BaseModel):
    title: str = "New conversation"


class Attachment(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    text: str = Field(max_length=120000)
    extension: str = Field(default="txt", max_length=10)
    size: int = Field(default=0, ge=0, le=MAX_UPLOAD_BYTES)
    truncated: bool = False


class SendMessageRequest(BaseModel):
    content: str = Field(default="", max_length=16000)
    mode: Literal["quick", "expert"] = "quick"
    attachments: list[Attachment] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_content(self):
        if not self.content.strip() and not self.attachments:
            raise ValueError("请输入问题或上传文档。")
        if sum(len(item.text) for item in self.attachments) > 240000:
            raise ValueError("附件文字总量不能超过 24 万字符。")
        return self


class ModelSettingsRequest(BaseModel):
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/settings/model")
async def get_model_settings():
    return await model_settings.read_public()


@app.post("/api/settings/model")
async def save_model_settings(payload: ModelSettingsRequest):
    return await model_settings.save(payload.api_key, payload.base_url, payload.model)


@app.post("/settings/model-form")
async def save_model_settings_form(request: Request):
    body = (await request.body()).decode("utf-8")
    form = {key: values[-1] for key, values in parse_qs(body).items()}
    await model_settings.save(
        form.get("api_key", ""),
        form.get("base_url", "https://api.deepseek.com"),
        form.get("model", "deepseek-chat"),
    )
    return RedirectResponse("/", status_code=303)


@app.get("/api/conversations")
async def list_conversations():
    return {"conversations": await store.list_conversations()}


@app.post("/api/conversations")
async def create_conversation(payload: CreateConversationRequest):
    conversation = await store.create_conversation(payload.title)
    return {"conversation": conversation}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    snapshot = await store.snapshot(conversation_id)
    if not snapshot["conversation"]:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return public_snapshot(snapshot)


def public_snapshot(snapshot):
    for message in snapshot["messages"]:
        message["attachments"] = [{key: value for key, value in item.items() if key != "text"} for item in message["attachments"]]
    return snapshot


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    deleted = await engine.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": True}


@app.post("/api/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, payload: SendMessageRequest):
    if not await store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        await engine.submit_user_message(conversation_id, payload.content, payload.mode,
                                         [item.model_dump() for item in payload.attachments])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    return {"accepted": True}


@app.post("/api/conversations/{conversation_id}/cancel")
async def cancel_generation(conversation_id: str):
    if not await store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"cancelled": await engine.cancel(conversation_id)}


@app.post("/api/documents/parse")
async def parse_document(file: UploadFile = File(...)):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="文件不能超过 8 MB。")
        return await asyncio.to_thread(extract_document_text, file.filename or "document", content)
    except DocumentParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()


@app.get("/api/conversations/{conversation_id}/events")
async def conversation_events(conversation_id: str, request: Request, after: int = 0):
    if not await store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def event_stream():
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")), 0)
        except ValueError:
            cursor = max(after, 0)
        earliest = await store.list_events(conversation_id)
        if cursor == 0 or (earliest and cursor < earliest[0]["seq"] - 1):
            snapshot = public_snapshot(await store.snapshot(conversation_id))
            cursor = snapshot["cursor"]
            event = {"seq": cursor, "type": "conversation_snapshot", "payload": snapshot}
            yield f"id: {cursor}\nevent: conversation_snapshot\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        while True:
            revision = store.revision
            events = await store.list_events(conversation_id, cursor)
            for event in events:
                cursor = event["seq"]
                yield f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if await request.is_disconnected():
                break
            if len(events) == 200:
                continue
            await store.wait_for_events(revision)
            if not await store.get_conversation(conversation_id):
                break
            yield ": keep-alive\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")


@app.get("/{path:path}")
async def spa(path: str):
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not built")
    return FileResponse(index)
