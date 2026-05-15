from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Data Import Agent"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_db_path: str = "data/app.db"
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"
    import_db_host: str = "nas.biochao.cc"
    import_db_port: int = 5432
    import_db_user: str = "hanzy"
    import_db_password: str = "260203"
    import_db_name: str = "PMAID-FDZS"
    import_db_admin_name: str = "postgres"
    import_cached_summary_root: str = r"C:\Users\HP\AppData\Roaming\zspace\zspaceDocCache"
    import_downloaded_root: str = r"D:\tmp"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
