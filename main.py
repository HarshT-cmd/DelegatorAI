from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException

from config import get_google_chat_settings
from database import connect, init_db
from google_chat import process_google_chat_event, verify_google_chat_token
from models import GoogleChatEvent


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="DelegatorAI", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    with connect() as connection:
        connection.execute("SELECT 1").fetchone()
    return {"status": "ok", "database": "ok"}


@app.post("/google-chat/events")
def google_chat_events(
    event: GoogleChatEvent,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="A Google Chat bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="A Google Chat bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        settings = get_google_chat_settings()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        verify_google_chat_token(
            token.strip(),
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Google Chat token.") from exc

    if event.type == "APP_HOME":
        return {
            "action": {
                "navigations": [
                    {
                        "pushCard": {
                            "header": {"title": "DelegatorAI"},
                            "sections": [
                                {
                                    "widgets": [
                                        {
                                            "textParagraph": {
                                                "text": (
                                                    "Task coordination happens in "
                                                    "your project space. Reply in a "
                                                    "task follow-up thread to share "
                                                    "progress or report a blocker."
                                                )
                                            }
                                        }
                                    ]
                                }
                            ],
                        }
                    }
                ]
            }
        }

    process_google_chat_event(event, settings=settings)
    return {}
