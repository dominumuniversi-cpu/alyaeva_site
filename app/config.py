"""Настройки приложения. Секреты и параметры интеграций — в БД (app_settings),
в .env только то, что нужно для подключения к самой БД."""
import os


class Config:
    """Конфигурация из переменных окружения (.env рядом с проектом)."""

    DB_HOST = os.environ.get("DB_HOST", "sc-main-pg")
    DB_PORT = int(os.environ.get("DB_PORT", "5432"))
    DB_NAME = os.environ.get("DB_NAME", "alyaeva")
    DB_USER = os.environ.get("DB_USER", "alyaeva_app")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

    # Путь админки вынесен в переменную: не угадывается сканерами.
    ADMIN_SLUG = os.environ.get("ADMIN_SLUG", "")

    # Секрет для подписи cookie-сессий
    SECRET_KEY = os.environ.get("SECRET_KEY", "")

    # Куда складывать загруженные фото (вне webroot, отдаётся только через приложение)
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")

    MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))

    @property
    def dsn(self) -> str:
        """Строка подключения к PostgreSQL."""
        return (
            f"host={self.DB_HOST} port={self.DB_PORT} dbname={self.DB_NAME} "
            f"user={self.DB_USER} password={self.DB_PASSWORD}"
        )


config = Config()
