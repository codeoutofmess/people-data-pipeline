"""What a valid row looks like in each source file. Pydantic checks types and rules for us
and explains in words what is wrong, which becomes the quarantine reason."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

# shortcut: a basic "something@something" check, not full email validation.
# Use the email-validator package if it matters.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+$"


class Row(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def blanks_are_missing(cls, data: dict) -> dict:
        # CSV has no "null": an empty cell arrives as "". Dropping it lets Pydantic report
        # "Field required" for required fields and use the default (None) for optional ones.
        return {k: v for k, v in data.items() if v not in ("", None)}


class Application(Row):
    application_id: str
    person_id: str
    full_name: str
    email: str = Field(pattern=EMAIL_PATTERN)
    source_channel: Literal["referral", "job_board", "careers_site", "agency", "university"]
    stage: Literal["screening", "interview", "hired", "rejected"]
    hired: bool
    applied_on: date
    decided_on: date | None = None

    @model_validator(mode="after")
    def hired_matches_stage(self) -> "Application":
        if self.hired != (self.stage == "hired"):
            raise ValueError("hired flag contradicts stage")
        return self


class Employee(Row):
    person_id: str
    team: str
    role: str
    manager_id: str | None = None
    start_date: date
    status: Literal["active", "on_leave"]


class PayRecord(Row):
    person_id: str
    period_start: date
    period_end: date
    gross_amount: Decimal = Field(ge=0)


class Review(Row):
    review_id: str
    person_id: str
    cycle: str
    rating: int = Field(ge=1, le=5)
    reviewer_id: str
    reviewed_on: date


def describe(error: ValidationError) -> str:
    """Turn Pydantic's error list into one readable line, e.g. 'rating: Input should be ...'."""
    return "; ".join(f"{e['loc'][0]}: {e['msg']}" if e["loc"] else e["msg"] for e in error.errors())
