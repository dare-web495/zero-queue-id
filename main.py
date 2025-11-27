from fastapi import FastAPI, Depends, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from datetime import date, timedelta
from database import get_session, create_db_and_tables
from models import Applicant, DailySlot
from scheduler import generate_slots_for_date
import os
import yaml
from dotenv import load_dotenv   

load_dotenv()  # this loads your .env file

# Load config.yaml
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

app = FastAPI(title=f"{config['business_name']} • {config['service_name']}")
templates = Jinja2Templates(directory="templates")

# === SendGrid Email ===
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "bookings@zeroqueue.app")
sendgrid_client = None
if SENDGRID_API_KEY:
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
        sendgrid_client = sendgrid.SendGridAPIClient(SENDGRID_API_KEY)
    except Exception as e:
        print("SendGrid import failed:", e)

# === Twilio SMS (optional) ===
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM")
twilio_client = None
if TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM:
    try:
        from twilio.rest import Client
        twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)
    except:
        pass

@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    today = date.today()
    for i in range(30):
        generate_slots_for_date(today + timedelta(days=i))


@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "config": config})

@app.get("/book", response_class=HTMLResponse)
async def book_form(request: Request, session: Session = Depends(get_session)):
    today = date.today()
    dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 31)]
    return templates.TemplateResponse("book.html", {"request": request, "dates": dates, "config": config})

@app.post("/book")
async def book_slot(full_name: str = Form(...), phone: str = Form(...), email: str = Form(...), appointment_date: str = Form(...), session: Session = Depends(get_session)):
    target_date = date.fromisoformat(appointment_date)
    generate_slots_for_date(target_date)

    slots = session.exec(select(DailySlot).where(DailySlot.date == target_date, DailySlot.booked < DailySlot.capacity).order_by(DailySlot.id)).all()
    if not slots:
        raise HTTPException(400, "No slots available on this date.")

    chosen_slot = slots[0]
    chosen_slot.booked += 1

    applicant = Applicant(full_name=full_name, phone=phone, email=email, appointment_date=target_date,
                          appointment_time=chosen_slot.time, slot_id=chosen_slot.id, confirmed=True)
    session.add(applicant)
    session.add(chosen_slot)
    session.commit()
    session.refresh(applicant)

    msg = f"Hello {full_name}! Your {config['service_name']} is confirmed for {target_date} at {chosen_slot.time}. Ref: {applicant.id}"

    # SMS
    if twilio_client:
        try:
            twilio_client.messages.create(body=msg, from_=TWILIO_FROM, to=phone)
            print(f"SMS sent to {phone}")
        except:
            print("SMS failed")

    # Email
    if sendgrid_client:
        try:
            message = Mail(from_email=FROM_EMAIL, to_emails=email,
                           subject=f"Confirmed: {config['service_name']}",
                           html_content=f"<h3>Hi {full_name}!</h3><p>Your booking is confirmed:</p><ul><li>Date: {target_date}</li><li>Time: {chosen_slot.time}</li><li>Ref: {applicant.id}</li></ul><p>See you soon!</p>")
            sendgrid_client.send(message)
            print(f"Email sent to {email}")
        except Exception as e:
            print("Email failed:", e)
    else:
        print("Email skipped (no SendGrid)")

    return RedirectResponse("/success", status_code=303)

@app.get("/success", response_class=HTMLResponse)
async def success(request: Request):
    return templates.TemplateResponse("success.html", {"request": request, "config": config})

@app.get("/admin")
async def admin(session: Session = Depends(get_session)):
    today = date.today()
    return {
        "today_booked": session.exec(select(Applicant).where(Applicant.appointment_date == today)).count(),
        "total_upcoming": session.exec(select(Applicant).where(Applicant.appointment_date >= today)).count(),
    }
