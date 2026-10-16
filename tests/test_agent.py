import json
from datetime import date

import pytest

from agent import interpret_and_record_reply, interpret_reply
from database import add_participant, add_task, connect, get_task, init_db
from models import (
    InterpretationKind,
    Participant,
    Task,
    TaskInterpretation,
    TaskStatus,
)
from task_tools import (
    DuplicateEventError,
    TaskOwnershipError,
    record_progress,
    set_task_status,
)


class FakeProvider:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.system_prompt = ""
        self.user_prompt = ""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return json.dumps(self.result)


@pytest.fixture
def seeded_database(tmp_path):
    database_path = tmp_path / "agent.db"
    init_db(database_path)
    add_participant(Participant(id="kabir", name="Kabir"), database_path)
    add_task(
        Task(
            id="payment",
            project_id="college-app",
            title="Connect the payment API",
            owner_id="kabir",
            deadline=date(2026, 11, 1),
            completion_criteria="API connected and payment flow tested",
        ),
        database_path,
    )
    return database_path


def interpretation(
    kind: InterpretationKind,
    status: TaskStatus,
    *,
    summary: str,
    blocker: str | None = None,
    complete: bool = False,
    clarification: bool = False,
    question: str | None = None,
    next_action: str = "Continue work and report progress.",
) -> dict[str, object]:
    return {
        "interpretation": kind.value,
        "progress_summary": summary,
        "proposed_status": status.value,
        "blocker": blocker,
        "completion_confirmed": complete,
        "needs_clarification": clarification,
        "clarification_question": question,
        "next_action": next_action,
    }


def test_interprets_partial_payment_api_work_as_blocked(seeded_database):
    task = get_task("payment", seeded_database)
    assert task is not None
    provider = FakeProvider(
        interpretation(
            InterpretationKind.BLOCKER,
            TaskStatus.BLOCKED,
            summary="API connection is done; payment-flow testing is blocked.",
            blocker="Waiting for test credentials",
            next_action="Ask the coordinator to identify and request the credentials owner.",
        )
    )

    result = interpret_reply(
        task, "Connected, but waiting for test credentials.", provider
    )

    assert result.proposed_status == TaskStatus.BLOCKED
    assert result.blocker == "Waiting for test credentials"
    assert not result.completion_confirmed
    assert "completion_criteria" in provider.user_prompt
    assert "untrusted" in provider.system_prompt


def test_interprets_progress_without_marking_complete(seeded_database):
    task = get_task("payment", seeded_database)
    assert task is not None
    provider = FakeProvider(
        interpretation(
            InterpretationKind.PROGRESS,
            TaskStatus.IN_PROGRESS,
            summary="The API connection is underway.",
        )
    )

    result = interpret_reply(task, "I have started connecting the API.", provider)

    assert result.proposed_status == TaskStatus.IN_PROGRESS
    assert not result.completion_confirmed


def test_interprets_completion_only_with_confirmation(seeded_database):
    task = get_task("payment", seeded_database)
    assert task is not None
    provider = FakeProvider(
        interpretation(
            InterpretationKind.COMPLETION,
            TaskStatus.COMPLETE,
            summary="The API is connected and payment flow is tested.",
            complete=True,
            next_action="No further action is required.",
        )
    )

    result = interpret_reply(
        task, "The API is connected and the full payment flow passed testing.", provider
    )

    assert result.interpretation == InterpretationKind.COMPLETION
    assert result.completion_confirmed
    assert result.proposed_status == TaskStatus.COMPLETE


def test_ambiguous_reply_requires_clarification_without_status_change(
    seeded_database,
):
    task = get_task("payment", seeded_database)
    assert task is not None
    provider = FakeProvider(
        interpretation(
            InterpretationKind.AMBIGUOUS,
            TaskStatus.PENDING,
            summary="The reply does not establish the task's progress.",
            clarification=True,
            question="Which part of the payment API work is complete?",
            next_action="Wait for clarification before changing task status.",
        )
    )

    result = interpret_reply(task, "It's mostly done.", provider)

    assert result.needs_clarification
    assert result.proposed_status == TaskStatus.PENDING
    assert result.clarification_question


def test_ambiguous_reply_is_stored_without_changing_task_or_audit(seeded_database):
    provider = FakeProvider(
        interpretation(
            InterpretationKind.AMBIGUOUS,
            TaskStatus.PENDING,
            summary="The reply does not establish the task's progress.",
            clarification=True,
            question="Which part of the payment API work is complete?",
            next_action="Wait for clarification before changing task status.",
        )
    )

    _, task = interpret_and_record_reply(
        task_id="payment",
        participant_id="kabir",
        reply="It's mostly done.",
        event_id="ambiguous-event",
        provider=provider,
        database_path=str(seeded_database),
    )

    assert task.status == TaskStatus.PENDING
    with connect(seeded_database) as connection:
        message_count = connection.execute(
            "SELECT COUNT(*) FROM messages WHERE event_id = 'ambiguous-event'"
        ).fetchone()[0]
        history_count = connection.execute(
            "SELECT COUNT(*) FROM status_history WHERE task_id = 'payment'"
        ).fetchone()[0]
    assert message_count == 1
    assert history_count == 0


def test_reply_is_persisted_with_status_audit(seeded_database):
    provider = FakeProvider(
        interpretation(
            InterpretationKind.BLOCKER,
            TaskStatus.BLOCKED,
            summary="API connection is done; testing awaits credentials.",
            blocker="Waiting for test credentials",
            next_action="Ask the coordinator to identify the credentials owner.",
        )
    )

    result, task = interpret_and_record_reply(
        task_id="payment",
        participant_id="kabir",
        reply="Connected, but waiting for test credentials.",
        event_id="event-1",
        provider=provider,
        database_path=str(seeded_database),
    )

    assert result.proposed_status == TaskStatus.BLOCKED
    assert task.status == TaskStatus.BLOCKED
    assert task.blocker == "Waiting for test credentials"
    with connect(seeded_database) as connection:
        message = connection.execute(
            "SELECT event_id, text FROM messages WHERE task_id = 'payment'"
        ).fetchone()
        history = connection.execute(
            """
            SELECT old_status, new_status, reason, source_message_id
            FROM status_history WHERE task_id = 'payment'
            """
        ).fetchone()
    assert message["event_id"] == "event-1"
    assert message["text"] == "Connected, but waiting for test credentials."
    assert history["old_status"] == "Pending"
    assert history["new_status"] == "Blocked"
    assert history["source_message_id"] is not None


def test_wrong_owner_cannot_record_reply(seeded_database):
    task = get_task("payment", seeded_database)
    assert task is not None
    reply_interpretation = TaskInterpretation.model_validate(
        interpretation(
            InterpretationKind.PROGRESS,
            TaskStatus.IN_PROGRESS,
            summary="Work is underway.",
        )
    )

    with pytest.raises(TaskOwnershipError):
        record_progress(
            task_id="payment",
            participant_id="someone-else",
            reply="I started.",
            interpretation=reply_interpretation,
            database_path=seeded_database,
        )


def test_duplicate_event_does_not_double_update(seeded_database):
    provider = FakeProvider(
        interpretation(
            InterpretationKind.PROGRESS,
            TaskStatus.IN_PROGRESS,
            summary="Work is underway.",
        )
    )
    interpret_and_record_reply(
        task_id="payment",
        participant_id="kabir",
        reply="I started.",
        event_id="duplicate-event",
        provider=provider,
        database_path=str(seeded_database),
    )

    with pytest.raises(DuplicateEventError):
        interpret_and_record_reply(
            task_id="payment",
            participant_id="kabir",
            reply="I started.",
            event_id="duplicate-event",
            provider=provider,
            database_path=str(seeded_database),
        )
    assert get_task("payment", seeded_database).status == TaskStatus.IN_PROGRESS
    with connect(seeded_database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM messages WHERE event_id = 'duplicate-event'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM status_history WHERE task_id = 'payment'"
        ).fetchone()[0] == 1


def test_complete_requires_explicit_confirmation(seeded_database):
    with pytest.raises(ValueError, match="explicit completion confirmation"):
        set_task_status(
            task_id="payment",
            new_status=TaskStatus.COMPLETE,
            reason="Attempted update",
            database_path=seeded_database,
        )
