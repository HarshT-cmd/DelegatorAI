from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token

from agent import CompletionProvider, interpret_and_record_reply
from config import GoogleChatSettings, get_google_chat_settings
from database import (
    connect,
    find_task_for_chat_thread,
    get_task,
    save_outbound_chat_message,
)
from models import GoogleChatEvent, TaskInterpretation
from task_tools import DuplicateEventError, TaskNotFoundError, ask_clarification


CHAT_BOT_SCOPE = "https://www.googleapis.com/auth/chat.bot"


class GoogleChatSender(Protocol):
    def send_message(
        self,
        *,
        space_id: str,
        text: str,
        thread_name: str | None,
        thread_key: str | None,
    ) -> "ChatMessageReceipt": ...


@dataclass(frozen=True)
class ChatMessageReceipt:
    message_name: str | None
    thread_name: str | None


class GoogleChatClient:
    def __init__(self, service: object) -> None:
        self._service = service

    @classmethod
    def from_settings(cls, settings: GoogleChatSettings) -> "GoogleChatClient":
        from googleapiclient.discovery import build

        if settings.service_account_file is not None:
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(
                str(settings.service_account_file),
                scopes=[CHAT_BOT_SCOPE],
            )
        else:
            import google.auth

            credentials, _ = google.auth.default(scopes=[CHAT_BOT_SCOPE])
        return cls(build("chat", "v1", credentials=credentials, cache_discovery=False))

    def send_message(
        self,
        *,
        space_id: str,
        text: str,
        thread_name: str | None,
        thread_key: str | None,
    ) -> ChatMessageReceipt:
        thread = {"name": thread_name} if thread_name else {"threadKey": thread_key}
        result = (
            self._service.spaces()
            .messages()
            .create(parent=space_id, body={"text": text, "thread": thread})
            .execute()
        )
        returned_thread = result.get("thread") or {}
        return ChatMessageReceipt(
            message_name=result.get("name"),
            thread_name=returned_thread.get("name"),
        )


def verify_google_chat_token(
    token: str,
    *,
    settings: GoogleChatSettings | None = None,
) -> dict[str, object]:
    active_settings = settings or get_google_chat_settings()
    claims = id_token.verify_oauth2_token(
        token,
        GoogleAuthRequest(),
        audience=active_settings.audience,
    )
    expected_email = active_settings.expected_service_account_email
    if expected_email and claims.get("email") != expected_email:
        raise ValueError("Google Chat token is not from the configured service account.")
    return claims


def send_chat_message(
    *,
    task_id: str,
    text: str,
    client: GoogleChatSender | None = None,
    database_path: Path | str | None = None,
    settings: GoogleChatSettings | None = None,
) -> ChatMessageReceipt:
    if not text.strip():
        raise ValueError("Google Chat message text cannot be empty.")
    task = get_task(task_id, database_path)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id!r} does not exist.")

    active_settings = settings or get_google_chat_settings()
    space_id = active_settings.project_spaces.get(task.project_id)
    if space_id is None:
        raise RuntimeError(
            f"No Google Chat space is configured for project {task.project_id!r}."
        )
    with connect(database_path) as connection:
        owner = connection.execute(
            "SELECT id, google_user_id FROM participants WHERE id = ?",
            (task.owner_id,),
        ).fetchone()
        thread_row = connection.execute(
            """
            SELECT chat_thread_name FROM messages
            WHERE task_id = ? AND direction = 'outbound'
              AND chat_space_id = ? AND chat_thread_name IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (task_id, space_id),
        ).fetchone()
    if owner is None:
        raise RuntimeError(f"Owner {task.owner_id!r} for task {task_id!r} is missing.")
    if not owner["google_user_id"]:
        raise RuntimeError(
            f"Owner {task.owner_id!r} has no Google Chat user ID configured."
        )

    thread_name = thread_row["chat_thread_name"] if thread_row else None
    active_client = client or GoogleChatClient.from_settings(active_settings)
    receipt = active_client.send_message(
        space_id=space_id,
        text=f"<{owner['google_user_id']}> {text}",
        thread_name=thread_name,
        thread_key=None if thread_name else f"delegatorai-{task_id}",
    )
    saved_thread_name = receipt.thread_name or thread_name
    if saved_thread_name is None:
        raise RuntimeError(
            "Google Chat accepted the message but returned no thread name; "
            "the reply cannot be safely correlated."
        )
    save_outbound_chat_message(
        task_id=task_id,
        participant_id=task.owner_id,
        space_id=space_id,
        text=text,
        message_name=receipt.message_name,
        thread_name=saved_thread_name,
        database_path=database_path,
    )
    return ChatMessageReceipt(receipt.message_name, saved_thread_name)


def process_google_chat_event(
    event: GoogleChatEvent,
    *,
    client: GoogleChatSender | None = None,
    provider: CompletionProvider | None = None,
    database_path: Path | str | None = None,
    settings: GoogleChatSettings | None = None,
) -> ChatMessageReceipt | None:
    if event.type != "MESSAGE" or event.message is None:
        return None
    message = event.message
    if message.sender.type == "BOT" or not message.text.strip():
        return None
    thread_name = message.thread.name if message.thread else None
    event_id = event.eventId or message.name
    if not thread_name or not event_id:
        return None

    active_settings = settings or get_google_chat_settings()
    match = find_task_for_chat_thread(
        space_id=message.space.name,
        thread_name=thread_name,
        database_path=database_path,
    )
    if match is None:
        return None
    task, participant = match
    if (
        active_settings.project_spaces.get(task.project_id) != message.space.name
        or not participant.google_user_id
        or participant.google_user_id != message.sender.name
    ):
        return None

    try:
        interpretation, _ = interpret_and_record_reply(
            task_id=task.id,
            participant_id=participant.id,
            reply=message.text,
            event_id=event_id,
            provider=provider,
            database_path=str(database_path) if database_path is not None else None,
        )
    except DuplicateEventError:
        return None
    response = _participant_response(interpretation)
    return send_chat_message(
        task_id=task.id,
        text=response,
        client=client,
        database_path=database_path,
        settings=active_settings,
    )


def _participant_response(interpretation: TaskInterpretation) -> str:
    if interpretation.needs_clarification:
        return ask_clarification(interpretation)
    if interpretation.proposed_status.value == "Blocked":
        return (
            f"Thanks for the update. I recorded the blocker: "
            f"{interpretation.blocker}. The coordinator will review next steps."
        )
    if interpretation.completion_confirmed:
        return "Thanks, I recorded the task as complete."
    return f"Thanks, I recorded your update: {interpretation.progress_summary}"
