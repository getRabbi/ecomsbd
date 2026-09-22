"""Shared customer definitions, matching Advanced Insights' all-order history.

Qualifying orders are every persisted shop order, including drafts/cancellations,
as in Insights.customers. Delivery/RTO counts are parcels, not order states.
Manual tags never enter these rules. No predictions or global thresholds.
"""

from enum import StrEnum

REPEAT_MIN_ORDERS = 2
NEW_DAYS = 30
INACTIVE_DAYS = 90
HIGH_VALUE_MIN_CUSTOMERS = 5
REPEAT_MIN_OUTCOMES = 2


class Segment(StrEnum):
    NEW = "NEW"
    REPEAT = "REPEAT"
    HIGH_VALUE = "HIGH_VALUE"
    INACTIVE = "INACTIVE"
    SUCCESSFUL_REPEAT = "SUCCESSFUL_REPEAT"
    REPEATED_RTO = "REPEATED_RTO"
    FOLLOW_UP_DUE = "FOLLOW_UP_DUE"
    ACTIVE_ORDERS = "ACTIVE_ORDERS"
