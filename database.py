from sqlmodel import SQLModel, create_engine, Session
from dotenv import load_dotenv
from typing import Generator
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./id_queue.db")

# Railway fallback for persistent SQLite
if DATABASE_URL == "sqlite:///./id_queue.db" and "RAILWAY" in os.getenv("RAILWAY_ENVIRONMENT", ""):
    DATABASE_URL = "sqlite:////app/id_queue.db"

engine = create_engine(DATABASE_URL, echo=False)

def create_db_and_tables():
    try:
        SQLModel.metadata.create_all(engine)
    except Exception as e:
        print(f"DB creation failed: {e}")

def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
