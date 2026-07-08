from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://game:game@db:5432/andrejsala"
    secret_key: str = "dev-secret-change-me"

    tick_seconds: int = 5
    base_tax: float = 0.35
    start_ip: float = 400.0
    daily_ip: float = 100.0
    key_task_bonus: float = 50.0  # rule 2.3: first team to do a region's key task each day
    total_days: int = 3
    region_min_minutes: float = 30.0  # rule 3.2: time in a region before betting on it

    admin_username: str = "admin"
    admin_password: str = "admin"


settings = Settings()
