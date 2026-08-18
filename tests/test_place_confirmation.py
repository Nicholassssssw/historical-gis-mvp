import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import confirm_places
from app.models import Place, Project
from app.schemas import PlaceSelectionConfirm


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def make_project_with_places(db):
    project = Project(title="測試", filename="test.txt", raw_text="經杭州，遙望天目山。")
    db.add(project)
    db.flush()
    passed = Place(
        project_id=project.id,
        route_order=1,
        original_name="杭州",
        normalized_name="杭州",
        sentence="經杭州。",
        route_role="passed",
        active=True,
    )
    mentioned = Place(
        project_id=project.id,
        route_order=2,
        original_name="天目山",
        normalized_name="天目山",
        sentence="遙望天目山。",
        route_role="mentioned_only",
        active=True,
    )
    db.add_all([passed, mentioned])
    db.commit()
    return project, passed, mentioned


def test_empty_confirmation_can_be_retried_then_saves_user_selection(db):
    project, passed, mentioned = make_project_with_places(db)

    for _ in range(2):
        with pytest.raises(HTTPException, match="至少把一個地名") as error:
            confirm_places(project.id, PlaceSelectionConfirm(place_ids=[]), db)
        assert error.value.status_code == 400

    result = confirm_places(
        project.id,
        PlaceSelectionConfirm(place_ids=[passed.id, mentioned.id]),
        db,
    )

    db.refresh(passed)
    db.refresh(mentioned)
    assert result["selected_count"] == 1
    assert result["mentioned_count"] == 1
    assert passed.user_selected is True
    assert mentioned.user_selected is True


def test_mentioned_only_selection_does_not_confirm_a_route(db):
    project, passed, mentioned = make_project_with_places(db)

    with pytest.raises(HTTPException, match="至少把一個地名"):
        confirm_places(
            project.id,
            PlaceSelectionConfirm(place_ids=[mentioned.id]),
            db,
        )

    db.refresh(passed)
    db.refresh(mentioned)
    assert passed.user_selected is False
    assert mentioned.user_selected is False
