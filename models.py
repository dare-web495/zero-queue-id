from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime
from datetime import date as DateType

class Applicant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str
    phone: str = Field(index=True)  # ← removed unique=True
    email: str
    appointment_date: DateType
    appointment_time: str
    slot_id: int
    confirmed: bool = False
    checked_in: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

class DailySlot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: DateType = Field(index=True)
    time: str
    capacity: int = 1
    booked: int = 0
