"""Database connection and initialization using SQLAlchemy."""

import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

try:
    from src.config.paths import get_app_data_dir
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from config.paths import get_app_data_dir

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database connections and sessions."""

    @staticmethod
    def _resolve_db_path(db_path: str) -> str:
        """Resolve a relative database path against the project root."""
        candidate = Path(db_path)
        if candidate.is_absolute():
            return str(candidate)

        resolved = (get_app_data_dir() / candidate).resolve()
        return str(resolved)

    def __init__(self, db_path: str = "./data/travian.db"):
        self.db_path = self._resolve_db_path(db_path)
        self.engine: Optional[Engine] = None
        self.SessionLocal = None
        self._ensure_directory()

    def _ensure_directory(self):
        """Create data directory if it doesn't exist."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    @property
    def backup_directory(self) -> Path:
        return Path(self.db_path).parent / "backups"

    def latest_backup(self) -> Path | None:
        """Return the newest backup file for this database, if one exists."""
        backups = sorted(
            self.backup_directory.glob(f"{Path(self.db_path).stem}_*.sqlite3"),
            key=lambda path: path.stat().st_mtime,
        )
        return backups[-1] if backups else None

    def backup_is_due(self, interval_hours: int) -> bool:
        """Check whether no backup exists within the configured interval."""
        latest = self.latest_backup()
        if latest is None:
            return True
        interval = max(interval_hours, 1)
        return datetime.now().timestamp() - latest.stat().st_mtime >= interval * 3600

    @staticmethod
    def _verify_sqlite_file(path: Path) -> None:
        with closing(sqlite3.connect(str(path))) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
        if result is None or result[0] != "ok":
            raise sqlite3.DatabaseError(
                f"SQLite integrity check failed for {path}: {result}"
            )

    def create_backup(self) -> Path:
        """Create and integrity-check a consistent SQLite backup."""
        if not Path(self.db_path).is_file():
            raise FileNotFoundError(f"Database file does not exist: {self.db_path}")

        self.backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = self.backup_directory / (
            f"{Path(self.db_path).stem}_{timestamp}.sqlite3"
        )
        try:
            with closing(sqlite3.connect(self.db_path)) as source:
                with closing(sqlite3.connect(str(backup_path))) as destination:
                    source.backup(destination)
            self._verify_sqlite_file(backup_path)
        except Exception:
            if backup_path.exists():
                backup_path.unlink()
            logger.exception("Database backup failed: %s", backup_path)
            raise

        logger.info("Database backup created: %s", backup_path)
        return backup_path

    def restore_backup(self, backup_path: str | Path) -> None:
        """Restore a verified backup, first backing up the current database."""
        source_path = Path(backup_path).resolve()
        destination_path = Path(self.db_path).resolve()
        if source_path == destination_path:
            raise ValueError("The backup path must be different from the database path.")
        if not source_path.is_file():
            raise FileNotFoundError(f"Backup file does not exist: {source_path}")

        self._verify_sqlite_file(source_path)
        if destination_path.is_file():
            self.create_backup()

        if self.engine is not None:
            self.engine.dispose()
        try:
            with closing(sqlite3.connect(str(source_path))) as source:
                with closing(sqlite3.connect(str(destination_path))) as destination:
                    source.backup(destination)
            self._verify_sqlite_file(destination_path)
        except Exception:
            logger.exception("Database restore failed from %s", source_path)
            raise

        logger.info("Database restored from: %s", source_path)

    def init(self) -> None:
        """Initialize the database connection and create tables."""
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            echo=False,
            connect_args={"check_same_thread": False},
        )

        try:
            from src.database.models import Base
        except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
            from database.models import Base

        Base.metadata.create_all(self.engine)

        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )

        logger.info(f"Database initialized at {self.db_path}")
    
    def get_session(self) -> Session:
        """Get a new database session."""
        if self.SessionLocal is None:
            raise RuntimeError("Database not initialized. Call init() first.")
        return self.SessionLocal()
    
    def execute_raw(self, query: str, params: dict = None) -> list:
        """Execute a raw SQL query and return rows when a result set is present."""
        if self.engine is None:
            raise RuntimeError("Database not initialized.")

        statement = str(query).strip()
        is_select = statement.lower().startswith("select")
        with self.engine.connect() as conn:
            result = conn.execute(text(query), params or {})
            if not is_select:
                conn.commit()
            if result.returns_rows:
                return [dict(row) for row in result.mappings().all()]
            return []
    
    def close(self):
        """Close the database connection."""
        if self.engine:
            self.engine.dispose()
            logger.info("Database connection closed.")
    
    def __enter__(self):
        self.init()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Global database instance
_db_manager: Optional[DatabaseManager] = None


def get_db(db_path: str = "./data/travian.db") -> DatabaseManager:
    """Get or create the global database manager instance."""
    global _db_manager
    resolved_path = DatabaseManager._resolve_db_path(db_path)
    if _db_manager is None or _db_manager.db_path != resolved_path:
        if _db_manager is not None:
            _db_manager.close()
        _db_manager = DatabaseManager(resolved_path)
        _db_manager.init()
    return _db_manager


def reset_db():
    """Reset the global database instance (useful for testing)."""
    global _db_manager
    if _db_manager:
        _db_manager.close()
    _db_manager = None
