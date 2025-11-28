from dotenv import load_dotenv
import os
from sqlalchemy import create_engine
from sqlmodel import SQLModel

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./id_queue.db")

# Railway-specific fallback
if DATABASE_URL == "sqlite:///./id_queue.db" and "RAILWAY" in os.getenv("RAILWAY_ENVIRONMENT", ""):
    DATABASE_URL = "sqlite:////app/id_queue.db"

engine = create_engine(DATABASE_URL, echo=False)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
