from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskStatus(StrEnum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    BLOCKED = "Blocked"
    COMPLETE = "Complete"


class InterpretationKind(StrEnum):
    PROGRESS = "progress"
    COMPLETION = "completion"
    BLOCKER = "blocker"
    DEPENDENCY = "dependency"
    AMBIGUOUS = "ambiguous"


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Participant(Schema):
    id: str
    name: str
    google_user_id: str | None = None
    email: str | None = None


class Task(Schema):
    id: str
    project_id: str
    title: str
    owner_id: str
    deadline: date
    completion_criteria: str
    status: TaskStatus = TaskStatus.PENDING
    blocker: str | None = None
    last_follow_up_at: datetime | None = None


class TaskInterpretation(Schema):
    interpretation: InterpretationKind
    progress_summary: str = Field(min_length=1, max_length=2000)
    proposed_status: TaskStatus
    blocker: str | None = Field(default=None, max_length=1000)
    completion_confirmed: bool
    needs_clarification: bool
    clarification_question: str | None = Field(default=None, max_length=1000)
    next_action: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_interpretation_consistency(self) -> "TaskInterpretation":
        if self.interpretation == InterpretationKind.AMBIGUOUS:
            if not self.needs_clarification or not self.clarification_question:
                raise ValueError(
                    "Ambiguous replies require a clarification question."
                )
        if self.needs_clarification and not self.clarification_question:
            raise ValueError("A clarification question is required.")
        if self.completion_confirmed and self.proposed_status != TaskStatus.COMPLETE:
            raise ValueError("Confirmed completion must propose Complete status.")
        if self.interpretation == InterpretationKind.BLOCKER:
            if self.proposed_status != TaskStatus.BLOCKED or not self.blocker:
                raise ValueError("Blocker interpretations must identify a blocker.")
        return self


class GoogleChatUser(Schema):
    model_config = ConfigDict(extra="ignore")

    name: str
    displayName: str | None = None
    type: str | None = None


class GoogleChatSpace(Schema):
    model_config = ConfigDict(extra="ignore")

    name: str


class GoogleChatThread(Schema):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    threadKey: str | None = None


class GoogleChatMessage(Schema):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    text: str = ""
    sender: GoogleChatUser
    space: GoogleChatSpace
    thread: GoogleChatThread | None = None


class GoogleChatEvent(Schema):
    model_config = ConfigDict(extra="allow")

    type: str
    eventId: str | None = None
    message: GoogleChatMessage | None = None
