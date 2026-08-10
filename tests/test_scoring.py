from app.geocoder import name_similarity, haversine_km


def test_name_similarity():
    assert name_similarity("臨安", "臨安") == 1.0
    assert name_similarity("臨安", "臨安縣") >= 0.8


def test_haversine():
    assert haversine_km(120, 30, 120, 30) == 0
