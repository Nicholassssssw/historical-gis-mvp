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
    project_additions = {
        "historical_period": "VARCHAR(120)",
        "extraction_total_reads": "INTEGER DEFAULT 0",
        "extraction_completed_reads": "INTEGER DEFAULT 0",
        "extraction_chunk_chars": "INTEGER",
        "extraction_partial_json": "TEXT",
    }
    missing_project_columns = {
        name: sql_type for name, sql_type in project_additions.items() if name not in columns
    }
    if missing_project_columns:
        with engine.begin() as connection:
            for name, sql_type in missing_project_columns.items():
                connection.execute(text(f"ALTER TABLE projects ADD COLUMN {name} {sql_type}"))

    inspector = inspect(engine)
    if "places" not in inspector.get_table_names():
        return
    place_columns = {column["name"] for column in inspector.get_columns("places")}
    additions = {
        "gis_decision": "VARCHAR(30)",
        "record_level": "VARCHAR(40)",
        "travel_status": "VARCHAR(40)",
        "location_status": "VARCHAR(40)",
        "alias_relation": "TEXT",
        "decision_reason": "TEXT",
        "previous_route_place": "VARCHAR(255)",
        "next_route_place": "VARCHAR(255)",
        "adjacency_type": "VARCHAR(40)",
    }
    missing = {name: sql_type for name, sql_type in additions.items() if name not in place_columns}
    if missing:
        with engine.begin() as connection:
            for name, sql_type in missing.items():
                connection.execute(text(f"ALTER TABLE places ADD COLUMN {name} {sql_type}"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
