from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_health() -> None:
resp = client.get("/health")
assert resp.status_code == 200
body = resp.json()
assert body["status"] == "ok"
def test_chat_validation() -> None:
resp = client.post("/chat", json={"query": ""})
assert resp.status_code == 422
