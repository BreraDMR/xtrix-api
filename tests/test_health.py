def test_public_health_shows_no_internals(client):
    r = client.get("/metrics/health")
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {"status", "timestamp", "database", "redis"}
    assert data["database"] == {"status": "healthy"}


def test_health_details_are_admin_only(client, make_user, headers_for):
    assert client.get("/metrics/health/details").status_code == 401
    assert client.get("/metrics/health/details", headers=headers_for(make_user("specialist"))).status_code == 403
    r = client.get("/metrics/health/details", headers=headers_for(make_user("admin")))
    assert r.status_code == 200
    assert "version" in r.json()["database"]


def test_readiness(client):
    assert client.get("/metrics/health/ready").json() == {"status": "ready"}


def test_health_details_include_system_metrics(client, make_user, headers_for):
    r = client.get("/metrics/health/details", headers=headers_for(make_user("admin")))
    system = r.json()["system"]
    assert "error" not in system, system
    assert system["memory"]["total_gb"] > 0
