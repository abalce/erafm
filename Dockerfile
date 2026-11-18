# Jeden obraz z całą aplikacją: API, worker i zbudowany frontend.
# Lokalnie używa go Docker Compose (API i worker osobno), na hostingu działa jako jedna usługa.

# --- 1. Frontend: budowanie do plików statycznych -------------------------------------
FROM node:24-slim AS web

WORKDIR /web

# Same zależności: ta warstwa przebudowuje się tylko po zmianie package*.json
COPY web/package.json web/package-lock.json web/.npmrc ./
RUN npm ci

COPY web/ ./
# Pusty adres API: strona i API są pod tym samym adresem.
# Nicki profili demo przychodzą z ustawień hostingu jako build arg (poza repozytorium).
ARG PUBLIC_DEMO_USERS=""
ENV PUBLIC_API_URL="" \
    PUBLIC_DEMO_USERS=$PUBLIC_DEMO_USERS
RUN npm run build


# --- 2. Backend z frontendem ------------------------------------------------------------
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

# Kompilacja .pyc przy instalacji (szybszy start), kopiowanie zamiast linków,
# Python z obrazu zamiast pobieranego przez uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Same zależności: ta warstwa przebudowuje się tylko po zmianie pyproject/uv.lock
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Kod aplikacji i migracje, potem instalacja samego pakietu backend
# (README.md jest wymagany, bo pyproject.toml go wskazuje)
COPY backend/README.md backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/src ./src
COPY --from=web /web/build ./static
# Prawo odczytu dla wszystkich: obraz nie zależy od uprawnień plików na dysku
RUN chmod -R a+rX pyproject.toml uv.lock README.md alembic.ini migrations src static
RUN uv sync --frozen --no-dev

# Aplikacja nie działa jako root
RUN useradd --create-home --uid 1000 app
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    STATIC_DIR=/app/static

EXPOSE 8000

# Najpierw migracje bazy, potem serwer; exec przekazuje sygnały (np. stop) do uvicorna.
# Hosting podaje port w zmiennej PORT, lokalnie domyślnie 8000.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
