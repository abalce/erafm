import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .db import engine

logger = logging.getLogger(__name__)

app = FastAPI(title="Erafm")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as e:
        logger.exception("Health check: błąd połączenia z bazą")
        raise HTTPException(status_code=503, detail="db_unavailable") from e
    return {"status": "ok", "db": "ok"}
