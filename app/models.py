from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="Untitled")
    filename: Mapped[str] = mapped_column(String(255), default="")
    historical_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(String(50), default="uploaded")
    places_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    places = relationship("Place", back_populates="project", cascade="all, delete-orphan")


class Place(Base):
    __tablename__ = "places"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    route_order: Mapped[int] = mapped_column(Integer, index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255))
    date_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sentence: Mapped[str] = mapped_column(Text, default="")
    route_role: Mapped[str] = mapped_column(String(50), default="uncertain")
    place_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    historical_region: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    selected_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    coord_class: Mapped[str] = mapped_column(String(30), default="insufficient")
    coord_score: Mapped[float] = mapped_column(Float, default=0.0)
    coord_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    project = relationship("Project", back_populates="places")
    candidates = relationship("Candidate", back_populates="place", cascade="all, delete-orphan")


class Candidate(Base):
    __tablename__ = "coordinate_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(100))
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    candidate_name: Mapped[str] = mapped_column(String(255))
    lon: Mapped[float] = mapped_column(Float)
    lat: Mapped[float] = mapped_column(Float)
    admin: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_score: Mapped[float] = mapped_column(Float, default=0.0)
    source_weight: Mapped[float] = mapped_column(Float, default=0.0)
    agreement_count: Mapped[int] = mapped_column(Integer, default=0)
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    place = relationship("Place", back_populates="candidates")
