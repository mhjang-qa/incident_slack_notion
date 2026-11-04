from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3

from ..domain.models import Evidence, SlackMessage, now


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def evidence_digest(evidence):
    # Collected-at and permalinks are volatile; edits and inclusion decisions are material.
    return digest([{ "key": e.message.key, "text": e.message.text, "payload": e.message.payload,
                    "included": e.included, "score": e.relevance_score, "type": e.message_type}
                   for e in evidence if e.included])


class Repository:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, channel TEXT NOT NULL, ts TEXT NOT NULL,
                    message_id TEXT, root TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'Detected',
                    sync_state TEXT NOT NULL DEFAULT 'Pending', error TEXT, retries INTEGER NOT NULL DEFAULT 0,
                    analyzed_digest TEXT, notion_page_id TEXT, notion_revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, stop_reason TEXT,
                    UNIQUE(workspace, channel, ts)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS event_message_id ON events(workspace,channel,message_id) WHERE message_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS raw (
                    event_id TEXT NOT NULL REFERENCES events(id), message_key TEXT NOT NULL, revision TEXT NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(event_id,message_key,revision)
                );
                CREATE TABLE IF NOT EXISTS snapshots(event_id TEXT PRIMARY KEY REFERENCES events(id), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL REFERENCES events(id),
                    report TEXT NOT NULL, response TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, stage TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS backfills (
                    id TEXT PRIMARY KEY, request TEXT NOT NULL, cursor TEXT NOT NULL,
                    state TEXT NOT NULL, last_result TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS backfill_items (
                    job_id TEXT NOT NULL REFERENCES backfills(id), event_id TEXT NOT NULL REFERENCES events(id),
                    done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job_id,event_id)
                );
            ''')
            columns = {row[1] for row in db.execute("PRAGMA table_info(analyses)")}
            if "evidence" not in columns:
                db.execute("ALTER TABLE analyses ADD COLUMN evidence TEXT NOT NULL DEFAULT '[]'")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def lock(self):
        # Serialize UI/CLI/scheduler even across processes. OS releases lock after a crash.
        with self.path.with_suffix(".lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another collection/sync is running; retry after it finishes") from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def detect(self, root, workspace, tz):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM events WHERE workspace=? AND channel=? AND (ts=? OR (message_id IS NOT NULL AND message_id=?))", (workspace, root.channel, root.ts, root.message_id)).fetchone()
            if row:
                db.execute("UPDATE events SET root=? WHERE id=?", (root.model_dump_json(), row["id"]))
                return row["id"]
            date = datetime.fromtimestamp(float(root.ts), tz=tz).strftime("%Y%m%d")
            prefix = f"DEP-{date}-"
            ids = db.execute("SELECT id FROM events WHERE id LIKE ?", (prefix + "%",)).fetchall()
            suffix = max([int(x[0].rsplit("-", 1)[1]) for x in ids] + [0]) + 1
            event_id = prefix + f"{suffix:03d}"
            db.execute("INSERT INTO events(id,workspace,channel,ts,message_id,root,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (event_id, workspace, root.channel, root.ts, root.message_id, root.model_dump_json(), now(), now()))
            return event_id

    def event(self, event_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise KeyError(event_id)
        return dict(row)

    def events(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM events ORDER BY ts DESC")]

    def update(self, event_id, **values):
        allowed = {"state", "sync_state", "error", "retries", "analyzed_digest", "notion_page_id", "notion_revision", "stop_reason"}
        if not values.keys() <= allowed:
            raise ValueError("Unknown event field")
        values["updated_at"] = now()
        with self.connection() as db:
            db.execute("UPDATE events SET " + ",".join(k + "=?" for k in values) + " WHERE id=?", (*values.values(), event_id))

    def save_evidence(self, event_id, evidence):
        with self.connection() as db:
            for e in evidence:
                raw = e.message.model_dump(mode="json", exclude={"collected_at", "url"})
                revision = digest(raw)
                db.execute("INSERT OR IGNORE INTO raw VALUES(?,?,?,?,?)", (event_id, e.message.key, revision, e.model_dump_json(), now()))
            db.execute("INSERT INTO snapshots VALUES(?,?) ON CONFLICT(event_id) DO UPDATE SET data=excluded.data", (event_id, dump([e.model_dump(mode="json") for e in evidence])))

    def evidence(self, event_id):
        with self.connection() as db:
            row = db.execute("SELECT data FROM snapshots WHERE event_id=?", (event_id,)).fetchone()
        return [Evidence.model_validate(x) for x in json.loads(row[0])] if row else []

    def raw(self, event_id):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM raw WHERE event_id=? ORDER BY created_at,message_key", (event_id,))]

    def add_analysis(self, event_id, report, response, model, prompt_version, fingerprint):
        snapshot = dump([e.model_dump(mode="json") for e in self.evidence(event_id)])
        with self.connection() as db:
            cursor = db.execute("INSERT INTO analyses(event_id,report,response,model,prompt_version,fingerprint,created_at,evidence) VALUES(?,?,?,?,?,?,?,?)", (event_id, report.model_dump_json(), response, model, prompt_version, fingerprint, now(), snapshot))
            db.execute("UPDATE events SET analyzed_digest=?,state=?,error=NULL,sync_state='Pending',updated_at=? WHERE id=?", (fingerprint, "Analyzed" if report.is_deployment else "Rejected", now(), event_id))
            return cursor.lastrowid

    def analyses(self, event_id):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM analyses WHERE event_id=? ORDER BY id DESC", (event_id,))]

    def get_value(self, key, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_value(self, key, value):
        with self.connection() as db:
            db.execute("INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, dump(value)))

    def log(self, stage, message, event_id=None):
        with self.connection() as db:
            db.execute("INSERT INTO logs(event_id,stage,message,created_at) VALUES(?,?,?,?)", (event_id, stage, message, now()))

    def logs(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 200")]

    def create_backfill(self, job_id, request):
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO backfills(id,request,cursor,state,created_at,updated_at) VALUES(?,?,?,'Pending',?,?)",
                       (job_id, dump(request), request["start_date"], now(), now()))
        return self.backfill(job_id)

    def backfill(self, job_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM backfills WHERE id=?", (job_id,)).fetchone()
            stats = db.execute("SELECT COUNT(*),COALESCE(SUM(done),0) FROM backfill_items WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        value = dict(row)
        value["request"] = json.loads(value["request"])
        value["last_result"] = json.loads(value["last_result"])
        value["discovered"], value["processed"] = stats
        return value

    def backfills(self):
        with self.connection() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM backfills ORDER BY created_at,id")]
        return [self.backfill(job_id) for job_id in ids]

    def update_backfill(self, job_id, cursor, state, result):
        with self.connection() as db:
            db.execute("UPDATE backfills SET cursor=?,state=?,last_result=?,updated_at=? WHERE id=?", (cursor,state,dump(result),now(),job_id))

    def track_backfill_item(self, job_id, event_id):
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO backfill_items(job_id,event_id) VALUES(?,?)", (job_id,event_id))

    def backfill_item_done(self, job_id, event_id):
        with self.connection() as db:
            row = db.execute("SELECT done FROM backfill_items WHERE job_id=? AND event_id=?", (job_id,event_id)).fetchone()
        return bool(row and row[0])

    def finish_backfill_item(self, job_id, event_id):
        with self.connection() as db:
            db.execute("UPDATE backfill_items SET done=1 WHERE job_id=? AND event_id=?", (job_id,event_id))

    def backfill_event_ids(self):
        with self.connection() as db:
            return {r[0] for r in db.execute("SELECT DISTINCT event_id FROM backfill_items")}
