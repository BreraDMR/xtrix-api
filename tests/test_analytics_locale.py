"""Insights and the benchmark refusal speak the caller's language, not English only."""
from tests.tenant_world import build_tenant


def insights(client, t, lang):
    r = client.get("/analytics/insights", headers={**t["headers"], "Accept-Language": lang})
    assert r.status_code == 200, r.text
    return {i["title"]: i["message"] for i in r.json()["insights"]}


def test_insights_follow_the_language(client, make_user, headers_for):
    t = build_tenant(client, make_user, headers_for, "Lingua", "+42070033")
    en, ru, cz = (insights(client, t, lang) for lang in ("en", "ru", "cz"))
    # a fresh salon has no returning clients, so retention is low in every language
    assert "Low Retention Rate" in en
    assert "Низкое удержание клиентов" in ru and "Low Retention Rate" not in ru
    assert "Nízké udržení klientů" in cz
    assert "Удержание за 30 дней" in ru["Низкое удержание клиентов"]
    assert len(en) == len(ru) == len(cz)


def test_benchmark_refusal_is_translated(client, make_user, headers_for):
    t = build_tenant(client, make_user, headers_for, "Bench", "+42070044")
    r = client.get("/analytics/benchmarks", headers={**t["headers"], "Accept-Language": "ru"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert body["reason"].startswith("Пока мало салонов для сравнения")
