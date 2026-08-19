import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import geocoder
from app.db import Base
from app.geocoder import haversine_km, name_similarity
from app.models import Place, Project
from app.providers import default_providers
from app.providers.base import CandidateResult


def test_name_similarity():
    assert name_similarity("臨安", "臨安") == 1.0
    assert name_similarity("臨安", "臨安縣") >= 0.8


def test_haversine():
    assert haversine_km(120, 30, 120, 30) == 0


def test_default_geocoding_sources_exclude_disabled_local_catalogs():
    assert [provider.name for provider in default_providers()] == [
        "CHGIS",
        "Wikidata",
        "OpenStreetMap",
        "Google Places",
    ]


def test_geocoding_keeps_all_selected_records_and_reports_database_progress(monkeypatch):
    class FakeCHGIS:
        name = "CHGIS"
        source_weight = 0.98

        async def search(self, name, **kwargs):
            if name == "杭州":
                return [CandidateResult("CHGIS", "杭州", 120.15, 30.28)]
            return []

    monkeypatch.setattr(geocoder, "default_providers", lambda: [FakeCHGIS()])
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    project = Project(title="測試", filename="test.txt", raw_text="經杭州，提及天目山。")
    db.add(project)
    db.flush()
    db.add_all([
        Place(
            project_id=project.id,
            route_order=1,
            original_name="杭州",
            normalized_name="杭州",
            sentence="經杭州。",
            route_role="passed",
            user_selected=True,
        ),
        Place(
            project_id=project.id,
            route_order=2,
            original_name="天目山",
            normalized_name="天目山",
            sentence="提及天目山。",
            route_role="mentioned_only",
            user_selected=True,
        ),
        Place(
            project_id=project.id,
            route_order=3,
            original_name="蘇州",
            normalized_name="蘇州",
            sentence="另載蘇州。",
            route_role="mentioned_only",
            user_selected=False,
        ),
    ])
    db.commit()
    events = []

    results = asyncio.run(geocoder.geocode_project(db, project, events.append))

    assert len(results) == 2
    assert [result["name"] for result in results] == ["杭州", "天目山"]
    assert results[0]["coord_class"] == "confirmed"
    assert results[0]["coordinate_selected"] is True
    assert results[1]["coord_class"] == "insufficient"
    assert results[1]["coordinate_selected"] is False
    assert any(
        event["event"] == "databases_started" and event["databases"] == ["CHGIS"]
        for event in events
    )
    assert any(
        event["event"] == "database_complete" and event["database"] == "CHGIS"
        for event in events
    )
    db.close()
