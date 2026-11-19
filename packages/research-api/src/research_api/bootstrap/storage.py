"""Open the service's own files: private folders, the database, and the check that it is current."""

from __future__ import annotations

from pathlib import Path

from ..adapters.files import (
    PrivacyError,
    create_private_file,
    ensure_private_dir,
    ensure_private_file,
)
from ..adapters.persistence import Database
from .settings import Settings, SettingsError, load_settings

UPGRADE_HINT = "run: research-api db upgrade"


def load_private_settings(path: Path) -> Settings:
    """``load_settings`` for a file the service is about to run from.

    The file decides where the service lives and which origin it trusts, so one that another
    account can read or change is refused, like the folders and the database. It names the
    ``chmod`` to run. A missing file is left to ``load_settings`` to report.
    """
    if path.exists():
        try:
            ensure_private_file(path)
        except PrivacyError as e:
            raise SettingsError(str(e)) from e
    return load_settings(path)


def storage_paths(settings: Settings) -> tuple[Path, Path]:
    if settings.data_dir is None or settings.db_path is None:
        raise SettingsError(
            "set data_dir and db_path in the settings file: the folders where the service keeps "
            "its data and its database"
        )
    return settings.data_dir, settings.db_path


def open_database(settings: Settings, *, create: bool = False) -> Database:
    """The service's SQLite database, after the privacy checks.

    Missing folders are made with mode 0700. An existing folder or file open to other accounts
    is refused. The database file itself is made (mode 0600) only when ``create`` is true, which
    only ``research-api db upgrade`` asks for.
    """
    data_dir, db_path = storage_paths(settings)
    try:
        ensure_private_dir(data_dir)
        ensure_private_dir(db_path.parent)
        if settings.log_dir is not None:
            ensure_private_dir(settings.log_dir)
        if create:
            create_private_file(db_path)
        if not db_path.exists():
            raise SettingsError(f"the database {db_path} does not exist; {UPGRADE_HINT}")
        ensure_private_file(db_path, with_sqlite_siblings=True)
    except PrivacyError as e:
        raise SettingsError(str(e)) from e
    return Database.open(db_path)


def open_current_database(settings: Settings) -> Database:
    """``open_database``, refusing a database whose tables are older than the code."""
    database = open_database(settings)
    try:
        current = database.is_current()
    except Exception as e:  # not a SQLite file, unreadable, locked
        database.close()
        raise SettingsError(f"cannot read the database {settings.db_path}: {e}") from e
    if not current:
        database.close()
        raise SettingsError(f"the database is not up to date; {UPGRADE_HINT}")
    return database


def read_secret(path: Path, *, what: str) -> str:
    """A key kept in its own file: private to this account, one non-empty line of text."""
    try:
        ensure_private_file(path)
        value = path.read_text(encoding="utf-8").strip()
    except PrivacyError as e:
        raise SettingsError(str(e)) from e
    except (OSError, UnicodeDecodeError) as e:
        raise SettingsError(f"cannot read {what}: {path}") from e
    if not value or any(ch.isspace() for ch in value):
        raise SettingsError(f"{what} {path} must hold one key and nothing else")
    return value
