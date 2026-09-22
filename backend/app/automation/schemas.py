import uuid
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

TRIGGERS = ("order.created", "order.status_changed")
ACTIONS = (
    "SEND_TEMPLATE",
    "CREATE_FOLLOWUP",
    "ADD_TAG",
    "REMOVE_TAG",
    "SELLER_NOTIFICATION",
    "CREATE_TASK",
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Condition(Input):
    field: Literal["status", "channel", "cod_amount_paisa"]
    op: Literal["eq", "ne", "gte", "lte"]
    value: str | int

    @model_validator(mode="after")
    def valid_comparison(self) -> Self:
        if self.field == "cod_amount_paisa":
            if type(self.value) is not int or self.value < 0:
                raise ValueError("COD condition needs non-negative integer paisa")
        elif self.op not in {"eq", "ne"} or not isinstance(self.value, str):
            raise ValueError("Text conditions support eq/ne only")
        return self


class RuleInput(Input):
    name: str = Field(min_length=1, max_length=100)
    trigger: Literal["order.created", "order.status_changed"]
    conditions: list[Condition] = Field(default_factory=list, max_length=8)
    action: Literal[
        "SEND_TEMPLATE",
        "CREATE_FOLLOWUP",
        "ADD_TAG",
        "REMOVE_TAG",
        "SELLER_NOTIFICATION",
        "CREATE_TASK",
    ]
    config: dict[str, Any]
    enabled: bool = False


class TemplateAction(Input):
    template_key: str = Field(min_length=1, max_length=80)
    locale: Literal["en", "bn"] = "bn"
    channel: Literal["EMAIL"] = "EMAIL"


class FollowupAction(Input):
    text: str = Field(min_length=1, max_length=1000)
    due_hours: int = Field(default=24, ge=1, le=720)
    assignee_id: uuid.UUID | None = None


class TagAction(Input):
    tag_id: uuid.UUID


class TaskAction(Input):
    text_en: str = Field(min_length=1, max_length=1000)
    text_bn: str = Field(min_length=1, max_length=1000)
    due_hours: int = Field(default=24, ge=1, le=720)


class NoticeAction(Input):
    title_en: str = Field(min_length=1, max_length=160)
    title_bn: str = Field(min_length=1, max_length=160)
    text_en: str = Field(min_length=1, max_length=1000)
    text_bn: str = Field(min_length=1, max_length=1000)


CONFIGS: dict[str, type[Input]] = {
    "SEND_TEMPLATE": TemplateAction,
    "CREATE_FOLLOWUP": FollowupAction,
    "ADD_TAG": TagAction,
    "REMOVE_TAG": TagAction,
    "SELLER_NOTIFICATION": NoticeAction,
    "CREATE_TASK": TaskAction,
}
