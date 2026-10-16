import asyncio
import json
from datetime import date

import httpx
import pytest

from config import GoogleChatSettings
from database import add_participant, add_task, connect, init_db, list_tasks
from google_chat import (
    ChatMessageReceipt,
    process_google_chat_event,
    send_chat_message,
)
from main import app
from models import (
    GoogleChatEvent,
    InterpretationKind,
    Participant,
    Task,
    TaskStatus,
)


class FakeChatClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send_message(
        self,
        *,
        space_id: str,
        text: str,
        thread_name: str | None,
        thread_key: str | None,
    ) -> ChatMessageReceipt:
        self.sent.append(
            {
                "space_id": space_id,
                "text": text,
                "thread_name": thread_name,
                "thread_key": thread_key,
            }
        )
        return ChatMessageReceipt(
            message_name=f"{space_id}/messages/{len(self.sent)}",
            thread_name=thread_name or f"{space_id}/threads/task-thread",
        )


class FakeProvider:
    def complete(self, _system_prompt: str, _user_prompt: str) -> str:
        assert _system_prompt
        assert _user_prompt
        return json.dumps(
            {
                "interpretation": InterpretationKind.BLOCKER.value,
                "progress_summary": "The API is connected; testing awaits credentials.",
                "proposed_status": TaskStatus.BLOCKED.value,
                "blocker": "Waiting for test credentials",
                "completion_confirmed": False,
                "needs_clarification": False,
                "clarification_question": None,
                "next_action": "Ask the coordinator to find the credentials owner.",
            }
        )


@pytest.fixture
def chat_setup(tmp_path):
    database_path = tmp_path / "chat.db"
    init_db(database_path)
    add_participant(
        Participant(
            id="kabir",
            name="Kabir",
            google_user_id="users/12345",
            email="kabir@example.edu",
        ),
        database_path,
    )
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
    settings = GoogleChatSettings(
        audience="https://example.test/google-chat/events",
        service_account_file=None,
        project_spaces={"college-app": "spaces/payment-team"},
        expected_service_account_email="chat@system.gserviceaccount.com",
    )
    return database_path, settings


def test_reply_from_owner_updates_task_and_sends_real_thread_reply(chat_setup):
    database_path, settings = chat_setup
    client = FakeChatClient()
    provider = FakeProvider()
    initial = send_chat_message(
        task_id="payment",
        text="Checking in on the payment API task.",
        client=client,
        database_path=database_path,
        settings=settings,
    )

    event = GoogleChatEvent.model_validate(
        {
            "type": "MESSAGE",
            "eventId": "chat-event-1",
            "message": {
                "name": "spaces/payment-team/messages/reply-1",
                "text": "Connected, but waiting for test credentials.",
                "sender": {"name": "users/12345", "type": "HUMAN"},
                "space": {"name": "spaces/payment-team"},
                "thread": {"name": initial.thread_name},
            },
        }
    )

    result = process_google_chat_event(
        event,
        client=client,
        provider=provider,
        database_path=database_path,
        settings=settings,
    )

    assert result is not None
    assert result.thread_name == initial.thread_name
    assert "Waiting for test credentials" in client.sent[-1]["text"]
    assert client.sent[-1]["text"].startswith("<users/12345>")
    assert list_tasks(database_path)[0].status == TaskStatus.BLOCKED
    with connect(database_path) as connection:
        inbound = connection.execute(
            "SELECT event_id, text FROM messages WHERE direction = 'inbound'"
        ).fetchone()
        audit = connection.execute(
            "SELECT old_status, new_status FROM status_history"
        ).fetchone()
    assert inbound["event_id"] == "chat-event-1"
    assert inbound["text"] == "Connected, but waiting for test credentials."
    assert tuple(audit) == ("Pending", "Blocked")


def test_unknown_sender_or_unexpected_space_is_ignored(chat_setup):
    database_path, settings = chat_setup
    client = FakeChatClient()

    first = send_chat_message(
        task_id="payment",
        text="Checking in.",
        client=client,
        database_path=database_path,
        settings=settings,
    )
    payload = {
        "type": "MESSAGE",
        "eventId": "ignored-event",
        "message": {
            "text": "Please mark this complete.",
            "sender": {"name": "users/unknown", "type": "HUMAN"},
            "space": {"name": "spaces/payment-team"},
            "thread": {"name": first.thread_name},
        },
    }

    result = process_google_chat_event(
        GoogleChatEvent.model_validate(payload),
        client=client,
        provider=FakeProvider(),
        database_path=database_path,
        settings=settings,
    )

    assert result is None
    assert len(client.sent) == 1
    assert list_tasks(database_path)[0].status == TaskStatus.PENDING


def test_duplicate_google_event_is_not_processed_twice(chat_setup):
    database_path, settings = chat_setup
    client = FakeChatClient()

    first = send_chat_message(
        task_id="payment",
        text="Checking in.",
        client=client,
        database_path=database_path,
        settings=settings,
    )
    event = GoogleChatEvent.model_validate(
        {
            "type": "MESSAGE",
            "eventId": "duplicate-event",
            "message": {
                "text": "Connected, but waiting for test credentials.",
                "sender": {"name": "users/12345", "type": "HUMAN"},
                "space": {"name": "spaces/payment-team"},
                "thread": {"name": first.thread_name},
            },
        }
    )
    args = {
        "client": client,
        "provider": FakeProvider(),
        "database_path": database_path,
        "settings": settings,
    }

    process_google_chat_event(event, **args)
    duplicate_result = process_google_chat_event(event, **args)

    assert duplicate_result is None
    assert len(client.sent) == 2
    with connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM messages WHERE event_id = 'duplicate-event'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM status_history"
        ).fetchone()[0] == 1


def test_google_chat_http_route_requires_bearer_token(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "http.db"))

    async def request_without_auth():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                return await client.post(
                    "/google-chat/events",
                    json={"type": "ADDED_TO_SPACE"},
                )

    response = asyncio.run(request_without_auth())

    assert response.status_code == 401


def test_google_chat_http_route_accepts_verified_lifecycle_event(
    monkeypatch, tmp_path
):
    settings = GoogleChatSettings(
        audience="https://example.test/google-chat/events",
        service_account_file=None,
        project_spaces={},
        expected_service_account_email=None,
    )
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "http-valid.db"))
    monkeypatch.setattr("main.get_google_chat_settings", lambda: settings)

    def verify_token(token: str, *, settings: GoogleChatSettings) -> dict[str, str]:
        assert token == "verified-token"
        assert settings == settings_expected
        return {"email": "verified-chat-service-account"}

    settings_expected = settings
    monkeypatch.setattr("main.verify_google_chat_token", verify_token)

    async def request_with_auth():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                return await client.post(
                    "/google-chat/events",
                    headers={"Authorization": "bEaReR verified-token"},
                    json={"type": "ADDED_TO_SPACE"},
                )

    response = asyncio.run(request_with_auth())

    assert response.status_code == 200
    assert response.json() == {}


def test_google_chat_http_route_renders_app_home_card(monkeypatch, tmp_path):
    settings = GoogleChatSettings(
        audience="https://example.test/google-chat/events",
        service_account_file=None,
        project_spaces={},
        expected_service_account_email=None,
    )
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "http-home.db"))
    monkeypatch.setattr("main.get_google_chat_settings", lambda: settings)
    monkeypatch.setattr(
        "main.verify_google_chat_token",
        lambda _token, *, settings: {"email": "verified-chat-service-account"},
    )

    async def request_app_home():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                return await client.post(
                    "/google-chat/events",
                    headers={"Authorization": "Bearer verified-token"},
                    json={"type": "APP_HOME"},
                )

    response = asyncio.run(request_app_home())

    assert response.status_code == 200
    assert response.json()["action"]["navigations"][0]["pushCard"]["header"][
        "title"
    ] == "DelegatorAI"


def test_real_chat_message_payload_allows_google_optional_fields():
    event = GoogleChatEvent.model_validate(
        {
            "type": "MESSAGE",
            "eventTime": "2026-10-16T00:00:00Z",
            "space": {"name": "spaces/abc", "type": "SPACE"},
            "message": {
                "name": "spaces/abc/messages/xyz",
                "text": "Progress update",
                "argumentText": "Progress update",
                "sender": {
                    "name": "users/12345",
                    "type": "HUMAN",
                    "displayName": "Kabir",
                    "domainId": "example.edu",
                },
                "space": {"name": "spaces/abc", "type": "SPACE"},
                "thread": {"name": "spaces/abc/threads/1", "threadKey": "task-1"},
            },
        }
    )

    assert event.message is not None
    assert event.message.sender.name == "users/12345"
    assert event.message.space.name == "spaces/abc"
