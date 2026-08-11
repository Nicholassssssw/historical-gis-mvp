import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def ensure_compatibility_schema():
    """Add nullable compatibility columns for databases created by older releases."""
    inspector = inspect(engine)
    if "projects" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("projects")}
    if "historical_period" not in columns:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE projects ADD COLUMN historical_period VARCHAR(120)"
            ))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
