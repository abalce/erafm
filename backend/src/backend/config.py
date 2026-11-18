from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    database_url: str
    lastfm_api_key: str
    cors_origins: list[str] = ["http://localhost:5173"]
    # Strefa, w której liczymy tygodnie i dni: timestampy w bazie są w UTC, a słuchanie
    # po północy czasu polskiego ma trafiać do właściwego dnia
    app_timezone: str = "Europe/Warsaw"
    # Folder ze zbudowanym frontendem. Ustawiony w obrazie Dockera: wtedy API podaje też
    # stronę i całość działa pod jednym adresem. Lokalnie frontend serwuje `npm run dev`.
    static_dir: Path | None = None
    # Worker kolejki w tym samym procesie co API. Na darmowym hostingu mamy jedną usługę;
    # lokalnie w Docker Compose worker jest osobnym kontenerem.
    run_worker_in_api: bool = False

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")


settings = Settings()
