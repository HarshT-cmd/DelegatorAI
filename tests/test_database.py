import sqlite3
from datetime import date

import pytest

from database import add_participant, add_task, get_task, init_db, list_participants
from models import Participant, Task, TaskStatus


def test_database_persists_participants_and_tasks(tmp_path):
    database_path = tmp_path / "delegatorai.db"
    init_db(database_path)

    participant = Participant(
        id="p1",
        name="Kabir",
        google_user_id="users/123",
        email="kabir@example.edu",
    )
    task = Task(
        id="t1",
        project_id="project-1",
        title="Connect the payment API",
        owner_id=participant.id,
        deadline=date(2026, 11, 1),
        completion_criteria="API connected and payment flow tested",
        status=TaskStatus.BLOCKED,
        blocker="Waiting for test credentials",
    )

    add_participant(participant, database_path)
    add_task(task, database_path)

    assert list_participants(database_path) == [participant]
    assert get_task("t1", database_path) == task


def test_schema_has_required_tables_and_status_constraint(tmp_path):
    database_path = tmp_path / "delegatorai.db"
    init_db(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        connection.execute(
            "INSERT INTO participants (id, name) VALUES ('p1', 'Kabir')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO tasks (
                    id, project_id, title, owner_id, deadline,
                    completion_criteria, status
                ) VALUES ('t1', 'project-1', 'Task', 'p1', '2026-11-01',
                          'Done', 'Unknown')
                """
            )

    assert {"participants", "tasks", "messages", "status_history"} <= tables


def test_task_owner_must_exist(tmp_path):
    database_path = tmp_path / "delegatorai.db"
    init_db(database_path)
    task = Task(
        id="t1",
        project_id="project-1",
        title="Task",
        owner_id="missing",
        deadline=date(2026, 11, 1),
        completion_criteria="Done",
    )

    with pytest.raises(sqlite3.IntegrityError):
        add_task(task, database_path)


def test_init_db_migrates_existing_message_table(tmp_path):
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE,
                task_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    init_db(database_path)
    init_db(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(messages)")
        }
    assert {
        "chat_space_id",
        "chat_message_name",
        "chat_thread_name",
    } <= columns
