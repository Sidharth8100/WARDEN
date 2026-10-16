"""Tests for sim/chaos_web synthetic test internet and ground truth generation."""

import httpx
import pytest
from httpx import ASGITransport

from chaos_web.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.mark.asyncio
async def test_health_check(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_robots_txt(client: httpx.AsyncClient):
    resp = await client.get("/robots.txt")
    assert resp.status_code == 200
    assert "Allow: /" in resp.text


@pytest.mark.asyncio
async def test_price_page_archetype(client: httpx.AsyncClient):
    page_id = "test-price-1"
    # Ensure clean state
    await client.post(f"/__reset__/{page_id}")

    resp = await client.get(f"/pages/price/{page_id}")
    assert resp.status_code == 200
    assert f"Product Item {page_id}" in resp.text
    assert "$99.99" in resp.text
    assert "data-timestamp=" in resp.text


@pytest.mark.asyncio
async def test_jobs_page_archetype(client: httpx.AsyncClient):
    page_id = "test-jobs-1"
    await client.post(f"/__reset__/{page_id}")

    resp = await client.get(f"/pages/jobs/{page_id}")
    assert resp.status_code == 200
    assert f"Careers at Organization {page_id}" in resp.text
    assert "Staff Backend Engineer" in resp.text
    assert "Systems Architect" in resp.text


@pytest.mark.asyncio
async def test_app_spa_page_archetype(client: httpx.AsyncClient):
    page_id = "test-spa-1"
    await client.post(f"/__reset__/{page_id}")

    resp = await client.get(f"/pages/app/{page_id}")
    assert resp.status_code == 200
    assert '<div id="root">' in resp.text
    assert "loading-spinner" in resp.text
    # Notice that the static response contains the JavaScript injection code
    assert "Active Enterprise Dashboard" in resp.text
    assert "setTimeout" in resp.text


@pytest.mark.asyncio
async def test_mutation_engine_and_ground_truth(client: httpx.AsyncClient):
    page_id = "truth-test-p1"
    await client.post(f"/__reset__/{page_id}")

    # Initial truth log is 404 because page hasn't been accessed or mutated yet
    resp = await client.get(f"/__truth__/{page_id}")
    assert resp.status_code == 404

    # Trigger a manual mutation
    mut_resp = await client.post(f"/__mutate__/price/{page_id}")
    assert mut_resp.status_code == 200
    mut_data = mut_resp.json()
    assert mut_data["status"] == "mutated"
    assert mut_data["kind"] in ("signal", "noise")

    # Fetch ground truth log
    truth_resp = await client.get(f"/__truth__/{page_id}")
    assert truth_resp.status_code == 200
    history = truth_resp.json()
    assert len(history) == 1
    assert history[0]["kind"] == mut_data["kind"]
    assert history[0]["description"] == mut_data["description"]
    assert "at" in history[0]

    # Mutate a few more times
    for _ in range(3):
        await client.post(f"/__mutate__/price/{page_id}")

    truth_resp2 = await client.get(f"/__truth__/{page_id}")
    assert len(truth_resp2.json()) == 4

    # Reset clears history
    reset_resp = await client.post(f"/__reset__/{page_id}")
    assert reset_resp.status_code == 200
    resp_after_reset = await client.get(f"/__truth__/{page_id}")
    assert resp_after_reset.status_code == 404
