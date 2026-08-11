import os
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

load_dotenv()

from .db import Base, engine, ensure_compatibility_schema, get_db
from .models import Candidate, Place, Project
from .schemas import PlaceCreate, PlaceUpdate
from .file_parser import actual_page_count, extract_text
from .extraction import extract_places_with_gemini
from .geocoder import candidate_to_dict, geocode_project
from .exporter import project_geojson
from .place_roles import MAPPED_ROUTE_ROLES, normalize_route_role
from .text_metrics import document_metrics, numeric_year_from_period

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
Path("./data").mkdir(exist_ok=True)
Base.metadata.create_all(bind=engine)
ensure_compatibility_schema()

app = FastAPI(title="Historical GIS MVP", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def project_or_404(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


def place_or_404(db: Session, place_id: int) -> Place:
    p = db.get(Place, place_id)
    if not p:
        raise HTTPException(404, "Place not found")
    return p


def place_dict(p: Place, include_candidates: bool = False):
    data = {
        "id": p.id,
        "project_id": p.project_id,
        "route_order": p.route_order,
        "original_name": p.original_name,
        "normalized_name": p.normalized_name,
        "date_text": p.date_text,
        "sentence": p.sentence,
        "route_role": normalize_route_role(p.route_role),
        "place_type": p.place_type,
        "historical_region": p.historical_region,
        "confidence": p.confidence,
        "selected_lon": p.selected_lon,
        "selected_lat": p.selected_lat,
        "coord_class": p.coord_class,
        "coord_score": p.coord_score,
        "coord_source": p.coord_source,
        "manual_override": p.manual_override,
        "active": p.active,
    }
    if include_candidates:
        data["candidates"] = [candidate_to_dict(c) for c in sorted(p.candidates, key=lambda x: x.total_score, reverse=True)]
    return data


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    base_url = str(request.base_url).rstrip("/")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__BASE_URL__", base_url))


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    return {
        "arcgis_api_key": os.getenv("ARCGIS_API_KEY", ""),
        "google_enabled": bool(os.getenv("GOOGLE_MAPS_API_KEY")),
        "gemini_enabled": bool(os.getenv("GEMINI_API_KEY")),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
    }


@app.post("/api/projects")
async def upload_project(
    file: Annotated[UploadFile, File(...)],
    title: Annotated[str | None, Form()] = None,
    historical_period: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
):
    content = await file.read()
    try:
        text = extract_text(file.filename or "upload.txt", content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not text.strip():
        raise HTTPException(400, "檔案未能抽取到文字。掃描PDF需要先做OCR。")

    period = (historical_period or "").strip()[:120] or None
    metrics = document_metrics(
        text,
        actual_pages=actual_page_count(file.filename or "upload.txt", content),
    )
    project = Project(
        title=title or Path(file.filename or "Untitled").stem,
        filename=file.filename or "upload",
        historical_year=numeric_year_from_period(period),
        historical_period=period,
        raw_text=text,
        stage="uploaded",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "title": project.title, "filename": project.filename,
            "historical_year": project.historical_year,
            "historical_period": project.historical_period,
            "stage": project.stage, "text_chars": len(project.raw_text), **metrics}


@app.post("/api/projects/{project_id}/extract")
def run_extraction(project_id: int, db: Session = Depends(get_db)):
    project = project_or_404(db, project_id)
    try:
        result = extract_places_with_gemini(project.raw_text, project.historical_period)
    except Exception as e:
        raise HTTPException(502, f"Gemini extraction failed: {e}")

    db.query(Candidate).filter(Candidate.place_id.in_(
        db.query(Place.id).filter(Place.project_id == project.id)
    )).delete(synchronize_session=False)
    db.query(Place).filter(Place.project_id == project.id).delete(synchronize_session=False)

    # Respect model order but normalize to a stable 1..N sequence for editing/routing.
    extracted = sorted(result.places, key=lambda x: x.route_order)
    for idx, item in enumerate(extracted, start=1):
        db.add(Place(
            project_id=project.id,
            route_order=idx,
            original_name=item.original_name.strip(),
            normalized_name=(item.normalized_name or item.original_name).strip(),
            date_text=item.date_text,
            sentence=item.sentence,
            route_role=normalize_route_role(item.route_role),
            place_type=item.place_type,
            historical_region=item.historical_region,
            confidence=item.confidence,
            active=True,
        ))
    project.stage = "review_places"
    project.places_confirmed = False
    db.commit()
    return {"count": len(extracted), "places": [place_dict(p) for p in db.query(Place).filter(Place.project_id == project.id).order_by(Place.route_order).all()]}


@app.get("/api/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    p = project_or_404(db, project_id)
    return {"id": p.id, "title": p.title, "filename": p.filename,
            "historical_year": p.historical_year,
            "historical_period": p.historical_period,
            "stage": p.stage,
            "places_confirmed": p.places_confirmed}


@app.get("/api/projects/{project_id}/places")
def list_places(project_id: int, candidates: bool = False, db: Session = Depends(get_db)):
    project_or_404(db, project_id)
    rows = db.query(Place).filter(Place.project_id == project_id, Place.active == True).order_by(Place.route_order).all()
    return [place_dict(p, include_candidates=candidates) for p in rows]


@app.post("/api/projects/{project_id}/places")
def create_place(project_id: int, payload: PlaceCreate, db: Session = Depends(get_db)):
    project = project_or_404(db, project_id)
    if project.places_confirmed:
        raise HTTPException(409, "地名已確認；如要更改，請先取消確認。")
    row = Place(
        project_id=project_id,
        route_order=payload.route_order,
        original_name=payload.original_name,
        normalized_name=payload.normalized_name or payload.original_name,
        date_text=payload.date_text,
        sentence=payload.sentence,
        route_role=payload.route_role,
        place_type=payload.place_type,
        historical_region=payload.historical_region,
        confidence=payload.confidence,
        active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return place_dict(row)


@app.patch("/api/places/{place_id}")
def update_place(place_id: int, payload: PlaceUpdate, db: Session = Depends(get_db)):
    p = place_or_404(db, place_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("route_role") is None and "route_role" in data:
        raise HTTPException(422, "經過／提及不可留空。")
    coordinate_change = "selected_lon" in data or "selected_lat" in data
    for k, v in data.items():
        setattr(p, k, v)
    if coordinate_change:
        p.manual_override = True
        if p.selected_lon is not None and p.selected_lat is not None:
            p.coord_class = "confirmed"
            p.coord_source = "manual"
            p.coord_score = 1.0
    db.commit()
    db.refresh(p)
    return place_dict(p, include_candidates=True)


@app.delete("/api/places/{place_id}")
def delete_place(place_id: int, db: Session = Depends(get_db)):
    p = place_or_404(db, place_id)
    p.active = False
    db.commit()
    return {"ok": True}


@app.post("/api/projects/{project_id}/confirm-places")
def confirm_places(project_id: int, db: Session = Depends(get_db)):
    project = project_or_404(db, project_id)
    count = db.query(Place).filter(Place.project_id == project_id, Place.active == True).count()
    if count == 0:
        raise HTTPException(400, "沒有地名可確認。")
    selected_count = db.query(Place).filter(
        Place.project_id == project_id,
        Place.active == True,
        Place.route_role.in_(MAPPED_ROUTE_ROLES),
    ).count()
    if selected_count == 0:
        raise HTTPException(400, "請至少把一個地名選為「經過」或「經過及提及」。")
    project.places_confirmed = True
    project.stage = "places_confirmed"
    db.commit()
    return {
        "ok": True,
        "count": count,
        "selected_count": selected_count,
        "mentioned_count": count - selected_count,
    }


@app.post("/api/projects/{project_id}/unconfirm-places")
def unconfirm_places(project_id: int, db: Session = Depends(get_db)):
    project = project_or_404(db, project_id)
    project.places_confirmed = False
    project.stage = "review_places"
    db.commit()
    return {"ok": True}


@app.post("/api/projects/{project_id}/geocode")
async def run_geocoding(project_id: int, db: Session = Depends(get_db)):
    project = project_or_404(db, project_id)
    if not project.places_confirmed:
        raise HTTPException(409, "請先由用戶確認地名，再進行經緯度配對。")
    project.stage = "geocoding"
    db.commit()
    try:
        result = await geocode_project(db, project)
    except Exception as e:
        project.stage = "geocode_error"
        db.commit()
        raise HTTPException(502, f"Geocoding failed: {e}")
    project.stage = "geocoded"
    db.commit()
    return {"count": len(result), "results": result}


@app.get("/api/places/{place_id}/candidates")
def list_candidates(place_id: int, db: Session = Depends(get_db)):
    p = place_or_404(db, place_id)
    rows = db.query(Candidate).filter(Candidate.place_id == place_id).order_by(Candidate.total_score.desc()).all()
    return [candidate_to_dict(c) for c in rows]


@app.post("/api/places/{place_id}/select-candidate/{candidate_id}")
def select_candidate(place_id: int, candidate_id: int, db: Session = Depends(get_db)):
    p = place_or_404(db, place_id)
    c = db.get(Candidate, candidate_id)
    if not c or c.place_id != p.id:
        raise HTTPException(404, "Candidate not found")
    p.selected_lon = c.lon
    p.selected_lat = c.lat
    p.coord_source = c.source
    p.coord_score = c.total_score
    p.coord_class = "confirmed"
    p.manual_override = True
    db.commit()
    return place_dict(p, include_candidates=True)


@app.get("/api/projects/{project_id}/map.geojson")
def map_geojson(project_id: int, download: bool = False, db: Session = Depends(get_db)):
    project = project_or_404(db, project_id)
    data = project_geojson(db, project)
    headers = {}
    if download:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project.title)[:80]
        headers["Content-Disposition"] = f'attachment; filename="{safe or "map"}.geojson"'
    return JSONResponse(data, media_type="application/geo+json", headers=headers)
