from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

Direction = Literal["pickup", "drop"]
ChangeType = Literal["reschedule", "cancel", "restore"]
RequestStatus = Literal[
    "APPLIED",
    "PENDING_APPROVAL",
    "APPROVED_APPLIED",
    "MANAGER_REJECTED",
    "APPROVED_NO_SEAT",
    "EXPIRED",
    "WITHDRAWN",
]


@dataclass(frozen=True)
class Company:
    id: str
    name: str
    timezone: str
    cutoff_minutes_pickup: int
    cutoff_minutes_drop: int
    max_seats_per_cab: int
    allow_escalation: bool

    def cutoff_for(self, direction: Direction) -> int:
        return self.cutoff_minutes_pickup if direction == "pickup" else self.cutoff_minutes_drop


@dataclass(frozen=True)
class Employee:
    id: str
    company_id: str
    name: str
    role: str
    route_id: str | None
    is_demo: bool
    email: str | None = None
    password_hash: str | None = None


@dataclass(frozen=True)
class Trip:
    id: str
    company_id: str
    route_id: str
    direction: Direction
    trip_date: date
    scheduled_at: datetime
    vehicle_capacity: int
    vehicle_no: str


@dataclass(frozen=True)
class TripOption:
    trip: Trip
    occupancy: int


@dataclass(frozen=True)
class RosterEntry:
    id: int
    company_id: str
    employee_id: str
    trip_date: date
    direction: Direction
    status: str
    trip: Trip


@dataclass(frozen=True)
class ShiftChangeRequest:
    id: str
    company_id: str
    employee_id: str
    conversation_id: str | None
    roster_id: int
    change_type: ChangeType
    from_trip_id: str
    to_trip_id: str | None
    status: RequestStatus
    reason_code: str | None
    reason_text: str | None
    dedupe_key: str
    approval_thread_id: str | None
    created_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
