from datetime import datetime, timedelta, date
from sqlmodel import Session, select
from database import engine
from models import DailySlot
import yaml
import os

# Load config at the top (this was missing!)
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

DAILY_CAPACITY = config['slots_per_day']
SLOT_DURATION = config['slot_duration_minutes']
START_HOUR = config['working_hours_start']
END_HOUR = config['working_hours_end']

def generate_slots_for_date(target_date: date):
    start = datetime(target_date.year, target_date.month, target_date.day, START_HOUR)
    end = datetime(target_date.year, target_date.month, target_date.day, END_HOUR)

    with Session(engine) as session:
        # Avoid duplicates
        if session.exec(select(DailySlot.date == target_date)).first():
            return

        current = start
        while current < end:
            slot = DailySlot(
                date=target_date,
                time=current.strftime("%H:%M"),
                capacity=1,
                booked=0
            )
            session.add(slot)
            current += timedelta(minutes=SLOT_DURATION)
        session.commit()
