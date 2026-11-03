import logging

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .db import engine

logger = logging.getLogger(__name__)

app = FastAPI(title="StatsMusic 2.0")


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as e:
        logger.exception("Health check: błąd połączenia z bazą: ", e)
        raise HTTPException(status_code=503, detail="db_unavailable")
    return {"status": "ok", "db": "ok"}
