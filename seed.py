from datetime import date, timedelta

from database import connect, init_db
from models import Participant, Task, TaskStatus


PARTICIPANTS = [
    Participant(id="p-kabir", name="Kabir"),
    Participant(id="p-maya", name="Maya"),
    Participant(id="p-anjali", name="Anjali"),
    Participant(id="p-rohan", name="Rohan"),
    Participant(id="p-leena", name="Leena"),
]

TASKS = [
    Task(
        id="task-payment-api",
        project_id="college-app",
        title="Connect the payment API",
        owner_id="p-kabir",
        deadline=date.today() + timedelta(days=7),
        completion_criteria="API connected and payment flow tested",
    ),
    Task(
        id="task-login-ui",
        project_id="college-app",
        title="Build the login screen",
        owner_id="p-maya",
        deadline=date.today() + timedelta(days=5),
        completion_criteria="Login screen implemented and reviewed",
        status=TaskStatus.IN_PROGRESS,
    ),
    Task(
        id="task-database-schema",
        project_id="college-app",
        title="Design the database schema",
        owner_id="p-anjali",
        deadline=date.today() + timedelta(days=4),
        completion_criteria="Schema documented and approved by the team",
    ),
    Task(
        id="task-test-plan",
        project_id="college-app",
        title="Prepare the test plan",
        owner_id="p-rohan",
        deadline=date.today() + timedelta(days=8),
        completion_criteria="Test cases cover the main user flows",
    ),
    Task(
        id="task-project-demo",
        project_id="college-app",
        title="Prepare the project demo",
        owner_id="p-leena",
        deadline=date.today() + timedelta(days=12),
        completion_criteria="Demo script and slides are ready",
    ),
]


def seed(database_path: str | None = None) -> None:
    init_db(database_path)
    with connect(database_path) as connection:
        connection.executemany(
            """
            INSERT OR IGNORE INTO participants (id, name, google_user_id, email)
            VALUES (?, ?, ?, ?)
            """,
            [
                (person.id, person.name, person.google_user_id, person.email)
                for person in PARTICIPANTS
            ],
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO tasks (
                id, project_id, title, owner_id, deadline,
                completion_criteria, status, blocker, last_follow_up_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    task.id,
                    task.project_id,
                    task.title,
                    task.owner_id,
                    task.deadline.isoformat(),
                    task.completion_criteria,
                    task.status.value,
                    task.blocker,
                    None,
                )
                for task in TASKS
            ],
        )


if __name__ == "__main__":
    seed()
    print(f"Seeded {len(PARTICIPANTS)} simulated participants and {len(TASKS)} tasks.")
