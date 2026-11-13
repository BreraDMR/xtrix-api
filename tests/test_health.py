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


def test_platform_revenue_total_is_admin_only(client, make_user, headers_for):
    # this sums completed visits across every business, so only the platform admin may see it
    params = {"start": "2026-01-01T00:00:00Z", "end": "2026-12-31T23:59:59Z"}
    assert client.get("/metrics/revenue", params=params).status_code == 401
    solo = make_user("specialist")
    assert client.get("/metrics/revenue", params=params, headers=headers_for(solo)).status_code == 403
    owner = make_user("business_owner")
    assert client.get("/metrics/revenue", params=params, headers=headers_for(owner)).status_code == 403
    r = client.get("/metrics/revenue", params=params, headers=headers_for(make_user("admin")))
    assert r.status_code == 200
    assert "revenue" in r.json()
