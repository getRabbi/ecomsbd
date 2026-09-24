"""Automation inputs: V2 single-action rules and V3.4 workflow definitions.

A workflow definition is data, never code: a trigger, entry conditions and a
tree of steps drawn from closed lists (actions, condition fields, operators,
events). There is no expression language, template evaluation or URL field
anywhere in it.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------- V2 rules ---

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


# ---------------------------------------------------------- action configs ---


class TemplateAction(Input):
    template_key: str = Field(min_length=1, max_length=80)
    locale: Literal["en", "bn"] = "bn"
    channel: Literal["EMAIL", "WHATSAPP"] = "EMAIL"


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
    #: Who in the team sees it: operations, finance or inventory staff.
    audience: Literal["OPERATIONS", "FINANCE", "INVENTORY"] = "OPERATIONS"


class CourierAction(Input):
    #: A courier connected to the shop; booked through the V2 booking service.
    provider: Literal["steadfast", "pathao", "redx"]


class EmptyAction(Input):
    pass


class TeamTaskAction(Input):
    """A task for the team about whatever the trigger is about (V3.5)."""

    text_en: str = Field(min_length=1, max_length=1000)
    text_bn: str = Field(min_length=1, max_length=1000)
    due_hours: int = Field(default=24, ge=1, le=720)
    assignee_id: uuid.UUID | None = None


class DraftPurchaseOrderAction(Input):
    """A DRAFT purchase order to the item's preferred supplier. Never ordered (V3.5)."""

    quantity: int = Field(ge=1, le=100_000)


class LabelAction(Input):
    #: A short label kept on the order's automation metadata. Not the status,
    #: not money, not stock.
    label: str = Field(min_length=1, max_length=40, pattern=r"^[\w\- ঀ-৿]+$")


class WebhookAction(Input):
    #: Sent as ``automation.workflow`` to endpoints subscribed to it.
    label: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_\-.]+$")


CONFIGS: dict[str, type[Input]] = {
    "SEND_TEMPLATE": TemplateAction,
    "CREATE_FOLLOWUP": FollowupAction,
    "ADD_TAG": TagAction,
    "REMOVE_TAG": TagAction,
    "SELLER_NOTIFICATION": NoticeAction,
    "CREATE_TASK": TaskAction,
    "BOOK_COURIER": CourierAction,
    "PUSH_STORE_STATUS": EmptyAction,
    "RETRY_INTEGRATION_SYNC": EmptyAction,
    "SET_ORDER_LABEL": LabelAction,
    "TRIGGER_WEBHOOK": WebhookAction,
    "CREATE_TEAM_TASK": TeamTaskAction,
    "CREATE_DRAFT_PO": DraftPurchaseOrderAction,
}
WORKFLOW_ACTIONS = tuple(CONFIGS)

# ------------------------------------------------------ triggers & subjects ---

#: Seller-facing triggers and the subject each one is about.
#: ``order`` subjects also carry the order's customer.
TRIGGER_SUBJECTS: dict[str, str] = {
    "order.created": "order",
    "order.external_received": "order",
    "order.confirmed": "order",
    "order.cancelled": "order",
    "order.status_changed": "order",
    "courier.booked": "order",
    "courier.status_changed": "order",
    "order.delivered": "order",
    "order.returned": "order",
    "cod.settled": "order",
    "payout.overdue": "shop",
    "reconciliation.issue": "shop",
    "followup.due": "shop",
    "inventory.low": "product",
    "integration.sync_failed": "integration",
    "customer.segment_entered": "customer",
    "customer.replied": "customer",
    "followup.completed": "customer",
    "purchase_order.ordered": "purchase_order",
    "purchase_order.partially_received": "purchase_order",
    "purchase_order.received": "purchase_order",
    "purchase_order.overdue": "shop",
    "supplier_payment.due": "shop",
    "transfer.completed": "shop",
}
WORKFLOW_TRIGGERS = tuple(TRIGGER_SUBJECTS)

#: What each subject kind can offer an action or a condition.
SUBJECT_PROVIDES: dict[str, frozenset[str]] = {
    "order": frozenset({"order", "customer"}),
    "customer": frozenset({"customer"}),
    "product": frozenset({"product"}),
    "integration": frozenset({"integration"}),
    "purchase_order": frozenset({"purchase_order"}),
    "shop": frozenset(),
}

#: The subject an action needs. ``None``: any trigger.
ACTION_NEEDS: dict[str, str | None] = {
    "SEND_TEMPLATE": "order",
    "CREATE_FOLLOWUP": "customer",
    "ADD_TAG": "customer",
    "REMOVE_TAG": "customer",
    "SELLER_NOTIFICATION": None,
    "CREATE_TASK": "order",
    "BOOK_COURIER": "order",
    "PUSH_STORE_STATUS": "order",
    "RETRY_INTEGRATION_SYNC": "integration",
    "SET_ORDER_LABEL": "order",
    "TRIGGER_WEBHOOK": None,
    "CREATE_TEAM_TASK": None,
    "CREATE_DRAFT_PO": "product",
}

#: Events a run can wait for, and the subject they are matched on.
WAIT_EVENTS: dict[str, str] = {
    "order.confirmed": "order",
    "courier.booked": "order",
    "order.delivered": "order",
    "customer.replied": "customer",
    "followup.completed": "customer",
}

# -------------------------------------------------------------- conditions ---

Op = Literal["eq", "ne", "in", "not_in", "gte", "lte", "has", "not_has", "is_true", "is_false"]

#: field -> (subject it needs, value kind, allowed operators)
FIELDS: dict[str, tuple[str | None, str, frozenset[str]]] = {
    "status": ("order", "enum", frozenset({"eq", "ne", "in", "not_in"})),
    "channel": ("order", "enum", frozenset({"eq", "ne", "in", "not_in"})),
    "cod_amount_paisa": ("order", "int", frozenset({"eq", "ne", "gte", "lte"})),
    "payment_method": ("order", "enum", frozenset({"eq", "ne"})),
    "courier": ("order", "enum", frozenset({"eq", "ne", "in", "not_in"})),
    "consignment_status": ("order", "enum", frozenset({"eq", "ne", "in", "not_in"})),
    "sku": ("order", "text", frozenset({"has", "not_has"})),
    "items_in_stock": ("order", "bool", frozenset({"is_true", "is_false"})),
    "external_source": ("order", "enum", frozenset({"eq", "ne", "in", "not_in"})),
    "customer_tag": ("customer", "tag", frozenset({"has", "not_has"})),
    "customer_segment": ("customer", "enum", frozenset({"has", "not_has"})),
    "customer_flag": ("customer", "enum", frozenset({"eq", "ne"})),
    "returned_parcels": ("customer", "int", frozenset({"eq", "gte", "lte"})),
    "transactional_consent": ("customer", "bool", frozenset({"is_true", "is_false"})),
    "marketing_consent": ("customer", "bool", frozenset({"is_true", "is_false"})),
    "stock_on_hand": ("product", "int", frozenset({"eq", "gte", "lte"})),
    "integration_provider": ("integration", "enum", frozenset({"eq", "ne", "in", "not_in"})),
    "sync_error_code": ("integration", "text", frozenset({"eq", "ne"})),
    #: Facts of the event itself.
    "entered_segment": ("customer", "enum", frozenset({"eq", "ne", "in", "not_in"})),
    "alert_kind": (None, "enum", frozenset({"eq", "ne"})),
}

MAX_STEPS = 30
MAX_ACTIONS = 15
MAX_WAITS = 5
MAX_NESTING = 4
MAX_DELAY_MINUTES = 30 * 24 * 60
STEP_ID = re.compile(r"^[a-z0-9_-]{1,32}$")
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class WorkflowCondition(Input):
    field: str
    op: Op
    value: str | int | list[str] | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> Self:
        spec = FIELDS.get(self.field)
        if spec is None:
            raise ValueError("Unknown condition field")
        _, kind, ops = spec
        if self.op not in ops:
            raise ValueError("Operator not supported for this field")
        if self.op in {"is_true", "is_false"}:
            self.value = None
        elif self.op in {"in", "not_in"}:
            if (
                not isinstance(self.value, list)
                or not 1 <= len(self.value) <= 20
                or not all(isinstance(v, str) and 0 < len(v) <= 80 for v in self.value)
            ):
                raise ValueError("A list condition needs 1-20 values")
        elif kind == "int":
            if type(self.value) is not int or not 0 <= self.value <= 10**12:
                raise ValueError("A number condition needs a non-negative whole number")
        elif not isinstance(self.value, str) or not 0 < len(self.value) <= 80:
            raise ValueError("A text condition needs a value")
        return self


class ConditionSet(Input):
    match: Literal["all", "any"] = "all"
    conditions: list[WorkflowCondition] = Field(default_factory=list, max_length=10)


class ConditionGroup(ConditionSet):
    #: One level of nested groups: (A and B) or (C and D).
    groups: list[ConditionSet] = Field(default_factory=list, max_length=5)


class ActionStep(Input):
    type: Literal["action"]
    id: str
    action: str
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_action(self) -> Self:
        if self.action not in CONFIGS:
            raise ValueError("Unsupported automation action")
        return self


class DelayStep(Input):
    type: Literal["delay"]
    id: str
    #: duration: wait N minutes. until_time: the next HH:MM in the shop's
    #: timezone (day_offset 1 = not today). followup_due: until the due time of
    #: the follow-up an earlier step created.
    mode: Literal["duration", "until_time", "followup_due"] = "duration"
    minutes: int | None = Field(default=None, ge=1, le=MAX_DELAY_MINUTES)
    time: str | None = None
    day_offset: Literal[0, 1] = 0
    step: str | None = None

    @model_validator(mode="after")
    def valid_mode(self) -> Self:
        if self.mode == "duration" and self.minutes is None:
            raise ValueError("A delay needs a number of minutes")
        if self.mode == "until_time" and (self.time is None or not TIME.match(self.time)):
            raise ValueError("A time delay needs a HH:MM time")
        if self.mode == "followup_due" and not self.step:
            raise ValueError("Choose the follow-up step to wait for")
        return self


class WaitStep(Input):
    type: Literal["wait_event"]
    id: str
    event: str
    timeout_minutes: int = Field(ge=1, le=MAX_DELAY_MINUTES)
    on_timeout: Literal["continue", "stop"] = "continue"

    @model_validator(mode="after")
    def valid_event(self) -> Self:
        if self.event not in WAIT_EVENTS:
            raise ValueError("Unsupported wait event")
        return self


class BranchStep(Input):
    type: Literal["branch"]
    id: str
    conditions: ConditionGroup
    then: list[Step] = Field(default_factory=list)
    otherwise: list[Step] = Field(default_factory=list, alias="else")

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)


Step = Annotated[ActionStep | DelayStep | WaitStep | BranchStep, Field(discriminator="type")]
BranchStep.model_rebuild()


class WorkflowDefinition(Input):
    trigger: str
    conditions: ConditionGroup = Field(default_factory=ConditionGroup)
    steps: list[Step] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_trigger(self) -> Self:
        if self.trigger not in TRIGGER_SUBJECTS:
            raise ValueError("Unsupported trigger")
        return self


class WorkflowInput(Input):
    name: str = Field(min_length=1, max_length=100)
    definition: WorkflowDefinition


class PublishInput(Input):
    enable: bool = True


class ToggleInput(Input):
    enabled: bool


class PreviewInput(Input):
    order_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None


class TestRunInput(PreviewInput):
    #: A real run fires real side effects; the seller must say so explicitly.
    confirm: bool = False


class RecipeInput(Input):
    locale: Literal["en", "bn"] = "bn"
    provider: Literal["steadfast", "pathao", "redx"] | None = None
    channel: Literal["EMAIL", "WHATSAPP"] = "EMAIL"
    enable: bool = True


# ------------------------------------------------------------- compilation ---


def dump(definition: WorkflowDefinition) -> dict[str, Any]:
    return definition.model_dump(mode="json", by_alias=True, exclude_none=True)


def walk(steps: list[Any], depth: int = 1) -> list[tuple[Any, int]]:
    """Every step in the tree with its nesting depth, in definition order."""
    found: list[tuple[Any, int]] = []
    for step in steps:
        found.append((step, depth))
        if isinstance(step, BranchStep):
            found += walk(step.then, depth + 1) + walk(step.otherwise, depth + 1)
    return found


def structural_problems(definition: WorkflowDefinition) -> list[str]:
    """Limits and references. Returns seller-safe codes; empty means sound."""
    problems: list[str] = []
    steps = walk(definition.steps)
    ids = [step.id for step, _ in steps]
    if len(steps) > MAX_STEPS:
        problems.append("TOO_MANY_STEPS")
    if any(not STEP_ID.match(i) for i in ids):
        problems.append("INVALID_STEP_ID")
    if len(set(ids)) != len(ids):
        problems.append("DUPLICATE_STEP_ID")
    if max(depth for _, depth in steps) > MAX_NESTING:
        problems.append("TOO_DEEPLY_NESTED")
    actions = [step for step, _ in steps if isinstance(step, ActionStep)]
    if len(actions) > MAX_ACTIONS:
        problems.append("TOO_MANY_ACTIONS")
    if not actions:
        problems.append("NO_ACTION")
    if sum(isinstance(step, DelayStep | WaitStep) for step, _ in steps) > MAX_WAITS:
        problems.append("TOO_MANY_WAITS")
    # One legacy task per run: the task table keys tasks by run.
    if sum(step.action in {"CREATE_TASK", "CREATE_TEAM_TASK"} for step in actions) > 1:
        problems.append("ONE_TASK_PER_WORKFLOW")
    if sum(step.action == "CREATE_DRAFT_PO" for step in actions) > 1:
        problems.append("ONE_DRAFT_PO_PER_WORKFLOW")
    if sum(step.action == "BOOK_COURIER" for step in actions) > 1:
        problems.append("ONE_BOOKING_PER_WORKFLOW")
    # A follow-up delay must point at a follow-up step that runs before it on
    # every path: the plan is compiled and checked below.
    plan = compile_plan(definition)
    for step, _ in steps:
        if isinstance(step, DelayStep) and step.mode == "followup_due":
            target = plan["steps"].get(step.step or "")
            if (
                target is None
                or target.get("action") != "CREATE_FOLLOWUP"
                or step.step not in _always_before(plan, step.id)
            ):
                problems.append("FOLLOWUP_STEP_NOT_BEFORE_DELAY")
    return problems


def compile_plan(definition: WorkflowDefinition) -> dict[str, Any]:
    """Flatten the tree: every step names the step that follows it.

    A branch arm continues with whatever follows the branch, like if/else in
    code. Links only ever point forward in the tree, so the plan is acyclic by
    construction and a run visits each step at most once — which is what lets
    ``(run, step)`` be the idempotency key of every side effect.
    """
    steps: dict[str, dict[str, Any]] = {}

    def chain(items: list[Any], after: str | None) -> str | None:
        following = after
        for step in reversed(items):
            data = step.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(step, BranchStep):
                data.pop("then", None)
                data.pop("else", None)
                data["then_next"] = chain(step.then, following)
                data["else_next"] = chain(step.otherwise, following)
            data["next"] = following
            steps[step.id] = data
            following = step.id
        return following

    entry = chain(definition.steps, None)
    return {"entry": entry, "steps": steps}


def successors(step: dict[str, Any]) -> list[str]:
    if step["type"] == "branch":
        return [s for s in (step.get("then_next"), step.get("else_next")) if s]
    return [step["next"]] if step.get("next") else []


def _always_before(plan: dict[str, Any], target: str) -> set[str]:
    """Steps on every path from the entry to ``target`` (dominators)."""
    paths: list[list[str]] = []

    def visit(node: str | None, trail: list[str]) -> None:
        if node is None or len(paths) > 200:
            return
        if node == target:
            paths.append(trail)
            return
        for nxt in successors(plan["steps"][node]):
            visit(nxt, [*trail, node])

    visit(plan["entry"], [])
    if not paths:
        return set()
    common = set(paths[0])
    for trail in paths[1:]:
        common &= set(trail)
    return common


def is_acyclic(plan: dict[str, Any]) -> bool:
    """Defence in depth for plans read back from storage."""
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return False
        if state.get(node) == 2:
            return True
        state[node] = 1
        for nxt in successors(plan["steps"][node]):
            if nxt not in plan["steps"] or not visit(nxt):
                return False
        state[node] = 2
        return True

    return plan["entry"] is None or visit(plan["entry"])


def legacy_definition(
    trigger: str, conditions: list[dict[str, Any]], action: str, config: dict[str, Any]
) -> dict[str, Any]:
    """A V2 rule as a one-step workflow."""
    return {
        "trigger": trigger,
        "conditions": {"match": "all", "conditions": conditions, "groups": []},
        "steps": [{"type": "action", "id": "s1", "action": action, "config": config}],
    }
