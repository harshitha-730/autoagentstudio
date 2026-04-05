import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()


def _build_default_mysql_url() -> str:
    db_user = quote_plus(os.getenv("DB_USER", "root"))
    db_password = quote_plus(os.getenv("DB_PASSWORD", ""))
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "3306")
    db_name = os.getenv("DB_NAME", "autovision_studio")

    auth = db_user if not db_password else f"{db_user}:{db_password}"
    return f"mysql+mysqlconnector://{auth}@{db_host}:{db_port}/{db_name}"


def _resolve_database_url() -> str:
    return os.getenv("DATABASE_URL", _build_default_mysql_url())


def _create_database_engine():
    primary_url = _resolve_database_url()
    allow_sqlite_fallback = os.getenv("ALLOW_SQLITE_FALLBACK", "true").lower() == "true"
    sqlite_fallback_url = os.getenv("SQLITE_FALLBACK_URL", "sqlite:///./autovision_studio.db")

    try:
        mysql_engine = create_engine(primary_url, pool_pre_ping=True, future=True)
        with mysql_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return mysql_engine, primary_url, False
    except Exception as mysql_error:
        if not allow_sqlite_fallback:
            raise RuntimeError(
                "Could not connect to MySQL database. Configure DATABASE_URL/DB_* environment values."
            ) from mysql_error

        sqlite_engine = create_engine(
            sqlite_fallback_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
        with sqlite_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        print(
            "MySQL connection failed. Using SQLite fallback instead. "
            "Set ALLOW_SQLITE_FALLBACK=false to enforce MySQL-only mode."
        )
        return sqlite_engine, sqlite_fallback_url, True


ENGINE, ACTIVE_DATABASE_URL, USING_SQLITE_FALLBACK = _create_database_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=ENGINE, future=True)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
