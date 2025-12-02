from fastapi import FastAPI, Depends, HTTPException, Form, Request, Security
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlmodel import Session, select
from sqlalchemy import text
from typing import Generator
from datetime import date, timedelta
from database import get_session, create_db_and_tables
from models import Applicant, DailySlot
from scheduler import generate_slots_for_date
import os
import yaml
from dotenv import load_dotenv
import logging

load_dotenv()

# Environment fallbacks
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////app/id_queue.db")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "bookings@zeroqueue.app")

# Logging
logging.basicConfig(level=logging.INFO)

# Load config
try:
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
except Exception as e:
    logging.error(f"Config load failed: {e}")
    config = {
        "business_name": "Zero Queue Restaurant",
        "service_name": "Table Reservation",
        "icon": "🍽️",
        "tagline": "Reserve your table — no more waiting",
        "slots_per_day": 120,
        "slot_duration_minutes": 90,
        "working_hours_start": 11,
        "working_hours_end": 22,
        "primary_color": "amber"
    }

app = FastAPI(title=f"{config['business_name']} • {config['service_name']}")

# Templates
try:
    templates = Jinja2Templates(directory="templates")
except Exception:
    templates = None

# SendGrid (lazy)
sendgrid_client = None
if SENDGRID_API_KEY:
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
        sendgrid_client = sendgrid.SendGridAPIClient(SENDGRID_API_KEY)
    except Exception as e:
        logging.error(f"SendGrid failed: {e}")

# Admin Login
ADMIN_USER = "admin"
ADMIN_PASS = "zeroqueue2025"
security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Security(security)):
    if credentials.username != ADMIN_USER or credentials.password != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return True

# Error handlers
@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})

@app.exception_handler(Exception)
async def general_error(request: Request, exc: Exception):
    logging.error(f"500 error: {exc}")
    return JSONResponse(status_code=500, content={"detail": "Internal server error – try again"})

# Startup
@app.on_event("startup")
def on_startup():
    try:
        create_db_and_tables()
        today = date.today()
        for i in range(7):
            generate_slots_for_date(today + timedelta(days=i))
    except Exception as e:
        logging.error(f"Startup failed: {e}")

# Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "config": config})

@app.get("/book", response_class=HTMLResponse)
async def book_form(request: Request, session: Session = Depends(get_session)):
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
            raise HTTPException(400, "No slots available")
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
        # Email
        if sendgrid_client:
            try:
                msg = Mail(
                    from_email=FROM_EMAIL,
                    to_emails=email,
                    subject="Booking Confirmed",
                    html_content=f"<strong>Hello {full_name}! Your slot is {chosen_slot.time} on {target_date}.</strong>"
                )
                sendgrid_client.send(msg)
            except Exception as e:
                logging.error(f"Email failed: {e}")
        return RedirectResponse(f"/success?ref={applicant.id}", status_code=303)
    except Exception as e:
        logging.error(f"Booking failed: {e}")
        raise HTTPException(500, "Try again")

@app.get("/success", response_class=HTMLResponse)
async def success(request: Request, ref: int = None, session: Session = Depends(get_session)):
    applicant = None
    if ref:
        applicant = session.exec(select(Applicant).where(Applicant.id == ref)).first()
    return templates.TemplateResponse("success.html", {"request": request, "config": config, "applicant": applicant})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(credentials: HTTPBasicCredentials = Depends(security)):
    verify_admin(credentials)
    return RedirectResponse("/admin", status_code=303)

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, _: bool = Depends(verify_admin), session: Session = Depends(get_session)):
    today = date.today()
    todays_bookings = session.exec(
        select(Applicant).where(Applicant.appointment_date == today).order_by(Applicant.appointment_time)
    ).all()
    stats = {
        "today_booked": len(todays_bookings),
        "checked_in_today": len([b for b in todays_bookings if b.checked_in]),
        "show_up_rate": round((len([b for b in todays_bookings if b.checked_in]) / len(todays_bookings) * 100) if todays_bookings else 0),
    }
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "config": config,
        "stats": stats,
        "todays_bookings": todays_bookings
    })

@app.post("/update-capacity")
async def update_capacity(capacity: int = Form(...), _: bool = Depends(verify_admin)):
    config["slots_per_day"] = capacity
    with open("config.yaml", "w") as f:
        yaml.dump(config, f)
    return RedirectResponse("/admin", status_code=303)

@app.get("/checkin", response_class=HTMLResponse)
async def checkin_page(request: Request):
    return templates.TemplateResponse("checkin.html", {"request": request})

@app.post("/checkin")
async def checkin(ref: str = Form(...), session: Session = Depends(get_session)):
    applicant = session.exec(select(Applicant).where(Applicant.id == int(ref))).first()
    if not applicant:
        raise HTTPException(404, "Invalid QR code")
    if applicant.checked_in:
        return {"message": f"{applicant.full_name} already checked in!"}
    applicant.checked_in = True
    session.add(applicant)
    session.commit()
    return {"message": f"Welcome {applicant.full_name}! Checked in at {applicant.appointment_time}"}

@app.get("/reset-db")
async def reset_db(session: Session = Depends(get_session)):
    session.exec(text("DELETE FROM applicant"))
    session.exec(text("DELETE FROM daily_slot"))
    session.commit()
    return {"status": "Database cleared!"}
