from fastapi import FastAPI, Depends, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from sqlmodel import Session, select
from typing import Generator
from datetime import date, timedelta
from database import get_session, create_db_and_tables
from models import Applicant, DailySlot
from scheduler import generate_slots_for_date
import os
import yaml
from dotenv import load_dotenv
import logging

load_dotenv()  # Load env vars

import logging
logging.basicConfig(level=logging.INFO)

# Fallback env vars
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./id_queue.db")  # Default if missing
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "bookings@zeroqueue.app")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM")

# Logging to catch startup errors
logging.basicConfig(level=logging.INFO)

# Load config with fallback
try:
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
except Exception as e:
    logging.error(f"Config load failed: {e}")
    config = {
        'business_name': "Zero Queue System",
        'service_name': "Appointment Booking",
        'icon': "🆔",
        'tagline': "No more waiting in line – book your exact time slot",
        'slots_per_day': 240,
        'slot_duration_minutes': 10,
        'working_hours_start': 8,
        'working_hours_end': 16,
        'primary_color': "indigo"
    }  # Default config if file missing

app = FastAPI(title=f"{config['business_name']} • {config['service_name']}")

# Templates with fallback
try:
    templates = Jinja2Templates(directory="templates")
except Exception as e:
    logging.error(f"Templates load failed: {e}")
    templates = None  # Fallback, but app will crash on render – fix folder

# Lazy SendGrid import (only if key present)
sendgrid_client = None
if SENDGRID_API_KEY:
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
        sendgrid_client = sendgrid.SendGridAPIClient(SENDGRID_API_KEY)
    except Exception as e:
        logging.error(f"SendGrid setup failed: {e}")

# Lazy Twilio import (only if keys present)
twilio_client = None
if TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM:
    try:
        from twilio.rest import Client
        twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)
    except Exception as e:
        logging.error(f"Twilio setup failed: {e}")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": "Invalid input – check form data"})

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logging.error(f"500 error: {exc}")
    return JSONResponse(status_code=500, content={"detail": "Internal server error – try again"})

@app.on_event("startup")
def on_startup():
    try:
        create_db_and_tables()
        today = date.today()
        for i in range(30):
            generate_slots_for_date(today + timedelta(days=i))
    except Exception as e:
        logging.error(f"Startup failed: {e}")
        

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if templates is None:
        return HTMLResponse("<h1>Zero Queue System</h1><a href='/book'>Book Now</a>")
    return templates.TemplateResponse("index.html", {"request": request, "config": config})

@app.get("/book", response_class=HTMLResponse)
async def book_form(request: Request, session: Session = Depends(get_session)):
    if templates is None:
        return HTMLResponse("<h1>Book Form</h1><form action='/book' method='post'>Full Name: <input name='full_name'><br>Phone: <input name='phone'><br>Email: <input name='email'><br>Date: <input name='appointment_date' type='date'><br><button>Confirm</button></form>")
    today = date.today()
    dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 31)]
    return templates.TemplateResponse("book.html", {"request": request, "dates": dates, "config": config})

@app.post("/book")
async def book_slot(
    full_name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(...),
    appointment_date: str = Form(...),
    session: Session = Depends(get_session)
):
    try:
        target_date = date.fromisoformat(appointment_date)
        generate_slots_for_date(target_date)
        slots = session.exec(
            select(DailySlot)
            .where(DailySlot.date == target_date)
            .where(DailySlot.booked < DailySlot.capacity)
            .order_by(DailySlot.id)
        ).all()
        if not slots:
            raise HTTPException(400, "No slots available on this date. Try another day.")
        chosen_slot = slots[0]
        chosen_slot.booked += 1
        applicant = Applicant(
            full_name=full_name,
            phone=phone,
            email=email,
            appointment_date=target_date,
            appointment_time=chosen_slot.time,
            slot_id=chosen_slot.id,
            confirmed=True
        )
        session.add(applicant)
        session.add(chosen_slot)
        session.commit()
        session.refresh(applicant)
        message = f"Hello {full_name}! Your National ID appointment is confirmed for {target_date} at {chosen_slot.time}. Arrive 5 mins early. Ref: {applicant.id}"
        if twilio_client:
            try:
                twilio_client.messages.create(body=message, from_=TWILIO_FROM, to=phone)
            except Exception as e:
                logging.error(f"SMS failed: {e}")
        if sendgrid_client:
            try:
                email_message = Mail(
                    from_email=FROM_EMAIL,
                    to_emails=email,
                    subject="Your National ID Appointment is Confirmed",
                    html_content=f"<strong>{message}</strong>"
                )
                sendgrid_client.send(email_message)
            except Exception as e:
                logging.error(f"Email failed: {e}")
        return RedirectResponse("/success", status_code=303)
    except Exception as e:
        logging.error(f"Booking failed: {e}")
        raise HTTPException(500, "Something went wrong. Try again.")

@app.get("/success", response_class=HTMLResponse)
async def success(request: Request):
    if templates is None:
        return HTMLResponse("<h1>Booking Confirmed! 🎉</h1><p>Check your email.</p><a href='/'>Book Another</a>")
    return templates.TemplateResponse("success.html", {"request": request, "config": config})

@app.get("/admin")
async def admin_dashboard(session: Session = Depends(get_session)):
    today = date.today()
    stats = {
        "today_booked": session.exec(select(Applicant).where(Applicant.appointment_date == today)).count(),
        "total_upcoming": session.exec(select(Applicant).where(Applicant.appointment_date >= today)).count(),
    }
    return stats
