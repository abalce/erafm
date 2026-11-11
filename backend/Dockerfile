FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

# Kompilacja .pyc przy instalacji (szybszy start), kopiowanie zamiast linków,
# Python z obrazu zamiast pobieranego przez uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# 1. Same zależności: ta warstwa przebudowuje się tylko po zmianie pyproject/uv.lock
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2. Kod aplikacji i migracje, potem instalacja samego pakietu backend
#    (README.md jest wymagany, bo pyproject.toml go wskazuje)
COPY README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev

# Aplikacja nie działa jako root
RUN useradd --create-home --uid 1000 app
USER app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Najpierw migracje bazy, potem serwer; exec przekazuje sygnały (np. stop) do uvicorna
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
