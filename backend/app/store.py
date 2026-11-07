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
        self._changed = asyncio.Condition()
        self.revision = 0
        self._last_prune = time.monotonic()

    async def init(self):
        await asyncio.to_thread(self._init_sync)

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout = 5000")
        conn.execute("pragma foreign_keys = ON")
        return conn

    def _init_sync(self):
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute("pragma journal_mode = WAL")
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
            columns = {row["name"] for row in conn.execute("pragma table_info(messages)")}
            for name, default in (("mode", "quick"), ("attachments", "[]"), ("steps", "{}"),
                                  ("context_summary", ""), ("error", ""), ("format_version", "1")):
                if name not in columns:
                    conn.execute(f"alter table messages add column {name} text not null default '{default}'")
            conn.execute("create index if not exists messages_conversation on messages(conversation_id, created_at)")
            conn.execute("create index if not exists events_conversation on events(conversation_id, id)")
            conn.execute("create index if not exists events_created_at on events(created_at)")
            conn.execute("delete from events where created_at < ? and (message_id is null or message_id in "
                         "(select id from messages where status != 'streaming'))", (time.time() - 7 * 86400,))

    async def _notify(self):
        async with self._changed:
            self.revision += 1
            self._changed.notify_all()

    async def wait_for_events(self, revision, timeout=15):
        async with self._changed:
            try:
                await asyncio.wait_for(self._changed.wait_for(lambda: self.revision != revision), timeout)
            except TimeoutError:
                pass

    async def _durable(self, function, *args):
        # 取消时等待已提交的事务结束，避免终态之后又写入增量。
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            await self._notify()
            raise
        await self._notify()
        return result

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

    async def delete_conversation(self, conversation_id):
        return await asyncio.to_thread(self._delete_conversation_sync, conversation_id)

    def _delete_conversation_sync(self, conversation_id):
        with self._lock, closing(self._connect()) as conn, conn:
            row = conn.execute(
                "select id from conversations where id = ?",
                (conversation_id,),
            ).fetchone()
            if not row:
                return False
            conn.execute("delete from events where conversation_id = ?", (conversation_id,))
            conn.execute("delete from messages where conversation_id = ?", (conversation_id,))
            conn.execute("delete from conversations where id = ?", (conversation_id,))
        return True

    async def update_conversation_title(self, conversation_id, title):
        return await asyncio.to_thread(self._update_conversation_title_sync, conversation_id, title)

    def _update_conversation_title_sync(self, conversation_id, title):
        now = time.time()
        with self._lock, closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "update conversations set title = ?, updated_at = ? where id = ?",
                (title, now, conversation_id),
            )
        return cur.rowcount > 0

    async def add_message(self, conversation_id, role, content, status="complete", mode="quick", attachments=None):
        return await self._durable(self._add_message_sync, conversation_id, role, content, status, mode, attachments or [])

    def _add_message_sync(self, conversation_id, role, content, status, mode, attachments):
        now = time.time()
        row = {
            "id": uuid.uuid4().hex,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "mode": mode,
            "attachments": attachments,
            "steps": {},
            "context_summary": "",
            "error": "",
            "format_version": 2,
        }
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                """
                insert into messages (id, conversation_id, role, content, status, created_at, updated_at, mode, attachments, format_version)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, '2')
                """,
                (row["id"], conversation_id, role, content, status, now, now, mode, json.dumps(attachments, ensure_ascii=False)),
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
                select *
                from messages
                where conversation_id = ?
                order by created_at asc
                """,
                (conversation_id,),
            ).fetchall()
        return [self._message(row) for row in rows]

    @staticmethod
    def _message(row):
        message = dict(row)
        message["attachments"] = json.loads(message.get("attachments") or "[]")
        message["steps"] = json.loads(message.get("steps") or "{}")
        message["format_version"] = int(message.get("format_version", 1))
        if "<!-- FINAL_DECISION -->" in message["content"]:
            message["mode"] = "expert"
        return message

    async def snapshot(self, conversation_id):
        return await asyncio.to_thread(self._snapshot_sync, conversation_id)

    def _snapshot_sync(self, conversation_id):
        with self._lock, closing(self._connect()) as conn:
            conn.execute("begin")
            conversation = conn.execute("select * from conversations where id = ?", (conversation_id,)).fetchone()
            messages = conn.execute("select * from messages where conversation_id = ? order by created_at, rowid", (conversation_id,)).fetchall()
            cursor = conn.execute("select coalesce(max(id), 0) from events where conversation_id = ?", (conversation_id,)).fetchone()[0]
        return {"conversation": dict(conversation) if conversation else None,
                "messages": [self._message(row) for row in messages], "cursor": cursor}

    async def append_delta(self, conversation_id, message_id, delta):
        if delta:
            await self._durable(self._append_delta_sync, conversation_id, message_id, delta)

    def _append_delta_sync(self, conversation_id, message_id, delta):
        with self._lock, closing(self._connect()) as conn, conn:
            changed = conn.execute("update messages set content = content || ?, updated_at = ? where id = ? and status = 'streaming'",
                                   (delta, time.time(), message_id)).rowcount
            if changed:
                self._insert_event(conn, conversation_id, "assistant_delta", {"delta": delta}, message_id)

    @staticmethod
    def _insert_event(conn, conversation_id, event_type, payload, message_id=None):
        cursor = conn.execute("insert into events (conversation_id, message_id, type, payload, created_at) values (?, ?, ?, ?, ?)",
                              (conversation_id, message_id, event_type, json.dumps(payload, ensure_ascii=False), time.time()))
        return cursor.lastrowid

    async def save_step(self, conversation_id, message_id, key, step):
        await self._durable(self._save_step_sync, conversation_id, message_id, key, step)

    def _save_step_sync(self, conversation_id, message_id, key, step):
        with self._lock, closing(self._connect()) as conn, conn:
            row = conn.execute("select steps from messages where id = ?", (message_id,)).fetchone()
            if not row:
                return
            steps = json.loads(row["steps"])
            steps[key] = step
            conn.execute("update messages set steps = ?, updated_at = ? where id = ?", (json.dumps(steps, ensure_ascii=False), time.time(), message_id))
            if key == "problem_analyst" and step["status"] == "complete":
                conn.execute("update messages set context_summary = ? where id = ?", (step.get("content", "")[:4000], message_id))
            event_type = "debate_step_completed" if step["status"] == "complete" else "debate_step_started"
            self._insert_event(conn, conversation_id, event_type, {"key": key, **step}, message_id)

    async def finish_message(self, conversation_id, message_id, status="complete", error=""):
        await self._durable(self._finish_message_sync, conversation_id, message_id, status, error)

    def _finish_message_sync(self, conversation_id, message_id, status, error):
        with self._lock, closing(self._connect()) as conn, conn:
            changed = conn.execute("update messages set status = ?, error = ?, updated_at = ? where id = ? and status = 'streaming'", (status, error, time.time(), message_id)).rowcount
            if not changed:
                return
            self._insert_event(conn, conversation_id, "assistant_message_completed", {"status": status, "error": error}, message_id)
            if time.monotonic() - self._last_prune > 3600:
                conn.execute("delete from events where created_at < ? and (message_id is null or message_id in "
                             "(select id from messages where status != 'streaming'))", (time.time() - 7 * 86400,))
                self._last_prune = time.monotonic()

    async def recover_interrupted(self):
        await self._durable(self._recover_interrupted_sync)

    def _recover_interrupted_sync(self):
        with self._lock, closing(self._connect()) as conn, conn:
            for row in conn.execute("select id, conversation_id from messages where status = 'streaming'").fetchall():
                error = "服务已重启，本次生成已中断。可以重新发送。"
                conn.execute("update messages set status = 'interrupted', error = ? where id = ?", (error, row["id"]))
                self._insert_event(conn, row["conversation_id"], "assistant_message_completed", {"status": "interrupted", "error": error}, row["id"])

    async def add_event(self, conversation_id, event_type, payload=None, message_id=None):
        return await self._durable(
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
                limit 200
                """,
                (conversation_id, int(after_seq or 0)),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event["payload"])
            events.append(event)
        return events
