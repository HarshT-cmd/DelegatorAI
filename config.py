import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def get_database_path() -> Path:
    return Path(os.environ.get("DATABASE_PATH", "delegatorai.db"))


@dataclass(frozen=True)
class LLMSettings:
    api_key: str
    base_url: str
    model: str


def get_llm_settings() -> LLMSettings:
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY is not configured. Set it in the environment or .env file."
        )
    return LLMSettings(
        api_key=api_key,
        base_url=os.environ.get(
            "LLM_BASE_URL", "https://integrate.api.nvidia.com/v1"
        ),
        model=os.environ.get("LLM_MODEL", "openai/gpt-oss-20b"),
    )


@dataclass(frozen=True)
class GoogleChatSettings:
    audience: str
    service_account_file: Path | None
    project_spaces: dict[str, str]
    expected_service_account_email: str | None


def get_google_chat_settings() -> GoogleChatSettings:
    audience = os.environ.get("CHAT_AUDIENCE", "").strip()
    service_account_file = os.environ.get("CHAT_SERVICE_ACCOUNT_FILE", "").strip()
    spaces_setting = os.environ.get("CHAT_PROJECT_SPACES", "{}")
    if not audience:
        raise RuntimeError("CHAT_AUDIENCE is not configured.")
    try:
        project_spaces = json.loads(spaces_setting)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CHAT_PROJECT_SPACES must be valid JSON.") from exc
    if not isinstance(project_spaces, dict) or not all(
        isinstance(project_id, str)
        and project_id
        and isinstance(space_name, str)
        and space_name.startswith("spaces/")
        for project_id, space_name in project_spaces.items()
    ):
        raise RuntimeError(
            "CHAT_PROJECT_SPACES must map project IDs to spaces/ resource names."
        )
    return GoogleChatSettings(
        audience=audience,
        service_account_file=Path(service_account_file) if service_account_file else None,
        project_spaces=project_spaces,
        expected_service_account_email=os.environ.get(
            "CHAT_EXPECTED_SERVICE_ACCOUNT_EMAIL"
        )
        or None,
    )
