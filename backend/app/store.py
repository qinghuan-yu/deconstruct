import asyncio
from contextlib import closing
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


class ConversationStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()

    async def init(self):
        await asyncio.to_thread(self._init_sync)

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sync(self):
        with self._lock, closing(self._connect()) as conn, conn:
            conn.executescript(
                """
                create table if not exists conversations (
                    id text primary key,
                    title text not null,
                    created_at real not null,
                    updated_at real not null
                );
                create table if not exists messages (
                    id text primary key,
                    conversation_id text not null,
                    role text not null,
                    content text not null,
                    status text not null,
                    created_at real not null,
                    updated_at real not null,
                    foreign key(conversation_id) references conversations(id)
                );
                create table if not exists events (
                    id integer primary key autoincrement,
                    conversation_id text not null,
                    message_id text,
                    type text not null,
                    payload text not null,
                    created_at real not null,
                    foreign key(conversation_id) references conversations(id)
                );
                """
            )

    async def create_conversation(self, title="New conversation"):
        return await asyncio.to_thread(self._create_conversation_sync, title)

    def _create_conversation_sync(self, title):
        now = time.time()
        row = {"id": uuid.uuid4().hex, "title": title or "New conversation", "created_at": now, "updated_at": now}
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                "insert into conversations (id, title, created_at, updated_at) values (?, ?, ?, ?)",
                (row["id"], row["title"], row["created_at"], row["updated_at"]),
            )
        return row

    async def list_conversations(self):
        return await asyncio.to_thread(self._list_conversations_sync)

    def _list_conversations_sync(self):
        with self._lock, closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "select id, title, created_at, updated_at from conversations order by updated_at desc"
            ).fetchall()
        return [dict(row) for row in rows]

    async def get_conversation(self, conversation_id):
        return await asyncio.to_thread(self._get_conversation_sync, conversation_id)

    def _get_conversation_sync(self, conversation_id):
        with self._lock, closing(self._connect()) as conn, conn:
            row = conn.execute(
                "select id, title, created_at, updated_at from conversations where id = ?",
                (conversation_id,),
            ).fetchone()
        return dict(row) if row else None

    async def add_message(self, conversation_id, role, content, status="complete"):
        return await asyncio.to_thread(self._add_message_sync, conversation_id, role, content, status)

    def _add_message_sync(self, conversation_id, role, content, status):
        now = time.time()
        row = {
            "id": uuid.uuid4().hex,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "status": status,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                """
                insert into messages (id, conversation_id, role, content, status, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (row["id"], conversation_id, role, content, status, now, now),
            )
            conn.execute(
                "update conversations set updated_at = ? where id = ?",
                (now, conversation_id),
            )
        return row

    async def append_message(self, message_id, chunk):
        return await asyncio.to_thread(self._append_message_sync, message_id, chunk)

    def _append_message_sync(self, message_id, chunk):
        now = time.time()
        with self._lock, closing(self._connect()) as conn, conn:
            row = conn.execute("select content from messages where id = ?", (message_id,)).fetchone()
            if not row:
                raise KeyError(f"message not found: {message_id}")
            content = row["content"] + chunk
            conn.execute(
                "update messages set content = ?, updated_at = ? where id = ?",
                (content, now, message_id),
            )
        return content

    async def update_message_status(self, message_id, status):
        await asyncio.to_thread(self._update_message_status_sync, message_id, status)

    def _update_message_status_sync(self, message_id, status):
        now = time.time()
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                "update messages set status = ?, updated_at = ? where id = ?",
                (status, now, message_id),
            )

    async def list_messages(self, conversation_id):
        return await asyncio.to_thread(self._list_messages_sync, conversation_id)

    def _list_messages_sync(self, conversation_id):
        with self._lock, closing(self._connect()) as conn, conn:
            rows = conn.execute(
                """
                select id, conversation_id, role, content, status, created_at, updated_at
                from messages
                where conversation_id = ?
                order by created_at asc
                """,
                (conversation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    async def add_event(self, conversation_id, event_type, payload=None, message_id=None):
        return await asyncio.to_thread(
            self._add_event_sync,
            conversation_id,
            event_type,
            payload or {},
            message_id,
        )

    def _add_event_sync(self, conversation_id, event_type, payload, message_id):
        now = time.time()
        with self._lock, closing(self._connect()) as conn, conn:
            cur = conn.execute(
                """
                insert into events (conversation_id, message_id, type, payload, created_at)
                values (?, ?, ?, ?, ?)
                """,
                (conversation_id, message_id, event_type, json.dumps(payload, ensure_ascii=False), now),
            )
            seq = cur.lastrowid
        return {"seq": seq, "conversation_id": conversation_id, "message_id": message_id, "type": event_type, "payload": payload}

    async def list_events(self, conversation_id, after_seq=0):
        return await asyncio.to_thread(self._list_events_sync, conversation_id, after_seq)

    def _list_events_sync(self, conversation_id, after_seq):
        with self._lock, closing(self._connect()) as conn, conn:
            rows = conn.execute(
                """
                select id as seq, conversation_id, message_id, type, payload, created_at
                from events
                where conversation_id = ? and id > ?
                order by id asc
                """,
                (conversation_id, int(after_seq or 0)),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event["payload"])
            events.append(event)
        return events
