from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_create_and_get_item():
    r = client.post("/items/1", json={"name": "clavier", "price": 49.9})
    assert r.status_code == 201
    r = client.get("/items/1")
    assert r.status_code == 200
    assert r.json()["name"] == "clavier"


def test_duplicate_item_rejected():
    client.post("/items/2", json={"name": "souris", "price": 10})
    r = client.post("/items/2", json={"name": "souris", "price": 10})
    assert r.status_code == 409


def test_invalid_item_rejected():
    r = client.post("/items/3", json={"name": "", "price": -1})
    assert r.status_code == 422


def test_unknown_item():
    assert client.get("/items/999").status_code == 404
