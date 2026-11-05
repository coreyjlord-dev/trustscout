"""FastAPI entrypoint for the TrustScout control plane."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import DB
from app.routes import router
from app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings; the process uses the environment."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    current = settings or Settings.from_env()
    app = FastAPI(title="TrustScout", docs_url=None, redoc_url=None)
    app.state.settings = current
    app.state.db = DB(current.data_dir / "trustscout.sqlite")
    app.include_router(router)
    static = Path(__file__).parent / "static"
    market = current.root / "mockmarket"
    app.mount("/static", StaticFiles(directory=static), name="static")
    app.mount("/market/assets", StaticFiles(directory=market / "assets"), name="market-assets")
    app.mount("/market/stock", StaticFiles(directory=market / "stock"), name="market-stock")
    return app


app = create_app()
