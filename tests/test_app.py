"""The mock pipeline through the HTTP app, with the fake runner (no Docker)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.vultr_client import RECORDED
from app.main import create_app
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "oak-writing-desk": "low",
    "kestrel-k1000": "low",
    "fielding-wool-coat": "low",
    "seller-mismatch-lamp": "high",
    "recycled-lumen-m6": "high",
    "too-cheap-monitor": "high",
    "hidden-note-turntable": "high",
    "hostile-price-script": "high",
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTSCOUT_MODE", "mock")
    monkeypatch.setenv("TRUSTSCOUT_RUNNER", "fake")
    monkeypatch.setenv("TRUSTSCOUT_EAGER", "1")
    monkeypatch.setenv("TRUSTSCOUT_RECORD_PROMPTS", "1")
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "unit-test-placeholder")
    monkeypatch.delenv("TRUSTSCOUT_LOCAL_GUARD", raising=False)
    RECORDED.clear()
    settings = Settings(
        root=ROOT,
        data_dir=tmp_path,
        host_data_dir=tmp_path,
        public_base="http://localhost:8080",
        internal_base="http://web:8080",
        app_network="",
        runner="fake",
        code_image="trustscout-code",
        browser_image="trustscout-browser",
        proxy_image="trustscout-egress",
        eager=True,
    )
    app = create_app(settings)
    return TestClient(app)


def test_home_lists_eight_demos(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Northlot" in page.text
    assert page.text.count("Check this listing") == 8
    assert "Fake runner" in page.text


def test_full_mock_market_run(client):
    urls = "\n".join(f"http://localhost:8080/market/{slug}" for slug in EXPECTED)
    response = client.post("/runs", data={"urls": urls}, follow_redirects=False)
    assert response.status_code == 303
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    report = client.get(f"/runs/{run_id}/print")
    assert report.status_code == 200
    body = report.text
    assert "37%" in body
    assert "quickflip-deals-99" in body
    assert "lumen-m6.svg" in body
    for slug, risk in EXPECTED.items():
        pattern = rf'class="verdict risk-{risk}"[\s\S]{{0,700}}/market/{re.escape(slug)}'
        assert re.search(pattern, body), slug
    assert "unit-test-placeholder" not in body
    joined = "\n".join(RECORDED)
    assert "Ignore previous instructions" in joined
    vision_chunks = [chunk for chunk in RECORDED if "trustscout-task: vision" in chunk]
    assert vision_chunks
    for chunk in vision_chunks:
        assert "Ignore previous instructions" not in chunk
        assert "unit-test-placeholder" not in chunk
    approved = client.post(f"/runs/{run_id}/approve", data={"action": "message_seller"})
    assert approved.status_code == 200
    assert "did not send a message" in approved.text
    assert "did not buy anything" in approved.text
    health = client.get("/health")
    assert health.json()["ok"] is True


def test_listing_page_hides_the_injection(client):
    page = client.get("/market/hidden-note-turntable")
    assert page.status_code == 200
    assert "Ignore previous instructions" in page.text
    assert 'class="trap"' in page.text
    hostile = client.get("/market/hostile-price-script")
    assert "seller-price-script" in hostile.text
    assert "STAGE fork" in hostile.text
    assert "text/javascript" not in hostile.text
