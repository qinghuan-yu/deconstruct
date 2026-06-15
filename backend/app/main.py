import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .chat_engine import ChatEngine
from .store import ConversationStore


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
FRONTEND_DIR = BASE_DIR / "frontend"

store = ConversationStore(DATA_DIR / "chat.sqlite3")
engine = ChatEngine(store)


@asynccontextmanager
async def lifespan(app):
    await store.init()
    yield


app = FastAPI(title="Decision Arena", lifespan=lifespan)


class CreateConversationRequest(BaseModel):
    title: str = "New conversation"


class SendMessageRequest(BaseModel):
    content: str
    mode: str = "quick"


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/conversations")
async def list_conversations():
    return {"conversations": await store.list_conversations()}


@app.post("/api/conversations")
async def create_conversation(payload: CreateConversationRequest):
    conversation = await store.create_conversation(payload.title)
    return {"conversation": conversation}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    conversation = await store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"conversation": conversation, "messages": await store.list_messages(conversation_id)}


@app.post("/api/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, payload: SendMessageRequest):
    if not await store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    await engine.submit_user_message(conversation_id, payload.content, payload.mode)
    return {"accepted": True}


@app.get("/api/conversations/{conversation_id}/events")
async def conversation_events(conversation_id: str, request: Request, after: int = 0):
    if not await store.get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def event_stream():
        cursor = after
        while True:
            events = await store.list_events(conversation_id, cursor)
            for event in events:
                cursor = event["seq"]
                yield f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")


@app.get("/{path:path}")
async def spa(path: str):
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not built")
    return FileResponse(index)
