import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from config import get_database_path
from models import Participant, Task, TaskStatus


SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    google_user_id TEXT UNIQUE,
    email TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    owner_id TEXT NOT NULL REFERENCES participants(id),
    deadline TEXT NOT NULL,
    completion_criteria TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'In Progress', 'Blocked', 'Complete')),
    blocker TEXT,
    last_follow_up_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    participant_id TEXT NOT NULL REFERENCES participants(id),
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    chat_space_id TEXT,
    chat_message_name TEXT,
    chat_thread_name TEXT
);

CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    old_status TEXT NOT NULL
        CHECK (old_status IN ('Pending', 'In Progress', 'Blocked', 'Complete')),
    new_status TEXT NOT NULL
        CHECK (new_status IN ('Pending', 'In Progress', 'Blocked', 'Complete')),
    reason TEXT NOT NULL,
    source_message_id INTEGER REFERENCES messages(id),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_id ON tasks(owner_id);
CREATE INDEX IF NOT EXISTS idx_messages_task_id ON messages(task_id);
CREATE INDEX IF NOT EXISTS idx_status_history_task_id
    ON status_history(task_id, created_at);
"""


@contextmanager
def connect(database_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(database_path) if database_path is not None else get_database_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(database_path: Path | str | None = None) -> None:
    with connect(database_path) as connection:
        connection.executescript(SCHEMA)
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(messages)")
        }
        for column in ("chat_space_id", "chat_message_name", "chat_thread_name"):
            if column not in columns:
                connection.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_chat_thread
            ON messages(chat_space_id, chat_thread_name)
            """
        )


def add_participant(
    participant: Participant, database_path: Path | str | None = None
) -> None:
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO participants (id, name, google_user_id, email)
            VALUES (?, ?, ?, ?)
            """,
            (
                participant.id,
                participant.name,
                participant.google_user_id,
                participant.email,
            ),
        )


def add_task(task: Task, database_path: Path | str | None = None) -> None:
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, project_id, title, owner_id, deadline,
                completion_criteria, status, blocker, last_follow_up_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.project_id,
                task.title,
                task.owner_id,
                task.deadline.isoformat(),
                task.completion_criteria,
                task.status.value,
                task.blocker,
                task.last_follow_up_at.isoformat()
                if task.last_follow_up_at is not None
                else None,
            ),
        )


def list_participants(database_path: Path | str | None = None) -> list[Participant]:
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT id, name, google_user_id, email FROM participants ORDER BY id"
        ).fetchall()
    return [Participant.model_validate(dict(row)) for row in rows]


def list_tasks(database_path: Path | str | None = None) -> list[Task]:
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM tasks ORDER BY deadline, id"
        ).fetchall()
    return [_task_from_row(row) for row in rows]


def get_task(task_id: str, database_path: Path | str | None = None) -> Task | None:
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return _task_from_row(row) if row is not None else None


def list_project_tasks(
    project_id: str, database_path: Path | str | None = None
) -> list[Task]:
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY deadline, id",
            (project_id,),
        ).fetchall()
    return [_task_from_row(row) for row in rows]


def save_outbound_chat_message(
    *,
    task_id: str,
    participant_id: str,
    space_id: str,
    text: str,
    message_name: str | None,
    thread_name: str | None,
    database_path: Path | str | None = None,
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO messages (
                task_id, participant_id, direction, text, created_at,
                chat_space_id, chat_message_name, chat_thread_name
            ) VALUES (?, ?, 'outbound', ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                participant_id,
                text,
                created_at,
                space_id,
                message_name,
                thread_name,
            ),
        )
        connection.execute(
            "UPDATE tasks SET last_follow_up_at = ? WHERE id = ?",
            (created_at, task_id),
        )


def find_task_for_chat_thread(
    *,
    space_id: str,
    thread_name: str,
    database_path: Path | str | None = None,
) -> tuple[Task, Participant] | None:
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT t.*, p.id AS participant_id, p.name AS participant_name,
                   p.google_user_id, p.email
            FROM messages AS m
            JOIN tasks AS t ON t.id = m.task_id
            JOIN participants AS p ON p.id = t.owner_id
            WHERE m.direction = 'outbound'
              AND m.chat_space_id = ?
              AND m.chat_thread_name = ?
            ORDER BY m.id DESC
            LIMIT 1
            """,
            (space_id, thread_name),
        ).fetchone()
    if row is None:
        return None
    task = _task_from_row(row)
    participant = Participant(
        id=row["participant_id"],
        name=row["participant_name"],
        google_user_id=row["google_user_id"],
        email=row["email"],
    )
    return task, participant


def _task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        owner_id=row["owner_id"],
        deadline=date.fromisoformat(row["deadline"]),
        completion_criteria=row["completion_criteria"],
        status=TaskStatus(row["status"]),
        blocker=row["blocker"],
        last_follow_up_at=datetime.fromisoformat(row["last_follow_up_at"])
        if row["last_follow_up_at"] is not None
        else None,
    )
