"""Composition root: builds the use cases and wires the adapters to their ports."""

from __future__ import annotations

import os
import shutil
import tempfile
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import version
from pathlib import Path

from pydantic import ValidationError

from ..adapters.circuits import CircuitsAdapter
from ..adapters.clock import SystemClock
from ..adapters.docingest_ask import (
    OfflineAskEngine,
    OfflineDocumentReader,
    open_docingest,
)
from ..adapters.fake import FakeAskEngine, FakeDocumentReader, FixedClock
from ..adapters.hashing import ArgonPasswordHasher
from ..adapters.persistence import Database
from ..application.ask import AskDesk
from ..application.auth import (
    Authenticate,
    AuthServices,
    ChangePassword,
    EndSession,
    ListSessions,
    LoginThrottle,
    ManageUsers,
    SignIn,
    SignOut,
)
from ..application.circuits import (
    CheckCircuit,
    ParseCircuit,
    RunCircuit,
    ShowCircuitConventions,
)
from ..application.documents import DocumentServices
from ..application.status import ReportStatus
from ..domain.auth import LoginPolicy, SessionPolicy
from ..domain.status import Mode
from ..ports.ask import AskEngine
from ..ports.auth import PasswordHasher
from ..ports.clock import Clock
from ..ports.documents import DocumentReader
from .seed import SeedUser, default_seed_users
from .settings import Settings, SettingsError, is_loopback
from .storage import load_private_settings, open_current_database

CONFIG_ENV = "RESEARCH_API_CONFIG"
FAKE_ENV = "RESEARCH_API_FAKE"
FAKE_ASK_PAUSE_ENV = "RESEARCH_API_FAKE_ASK_PAUSE"
DEFAULT_FAKE_ASK_PAUSE_S = 0.6
MAX_FAKE_ASK_PAUSE_S = 10.0
WEB_DIR_ENV = "RESEARCH_API_WEB_DIR"


@dataclass(frozen=True)
class Container:
    mode: Mode
    settings: Settings
    version: str
    clock: Clock
    report_status: ReportStatus
    parse_circuit: ParseCircuit
    run_circuit: RunCircuit
    check_circuit: CheckCircuit
    circuit_conventions: ShowCircuitConventions
    auth: AuthServices
    asks: AskDesk
    documents: DocumentServices
    close: Callable[[], None]
    """Release the database. Idempotent; the HTTP app calls it when it shuts down."""


def build_auth(
    database: Database, settings: Settings, clock: Clock, hasher: PasswordHasher
) -> AuthServices:
    sessions = SessionPolicy(
        idle=timedelta(hours=settings.session_idle_hours),
        absolute=timedelta(days=settings.session_max_days),
    )
    throttle = LoginThrottle(
        attempts=database.attempts,
        clock=clock,
        policy=LoginPolicy(
            max_failures=settings.login_max_failures,
            window=timedelta(minutes=settings.login_window_minutes),
        ),
    )
    return AuthServices(
        sign_in=SignIn(
            users=database.users,
            sessions=database.sessions,
            hasher=hasher,
            clock=clock,
            throttle=throttle,
            policy=sessions,
        ),
        sign_out=SignOut(sessions=database.sessions),
        authenticate=Authenticate(
            users=database.users, sessions=database.sessions, clock=clock, policy=sessions
        ),
        change_password=ChangePassword(
            users=database.users,
            sessions=database.sessions,
            hasher=hasher,
            clock=clock,
            throttle=throttle,
        ),
        list_sessions=ListSessions(
            users=database.users, sessions=database.sessions, clock=clock, policy=sessions
        ),
        end_session=EndSession(users=database.users, sessions=database.sessions),
        users=ManageUsers(
            users=database.users, sessions=database.sessions, hasher=hasher, clock=clock
        ),
    )


def _live_ask_parts(settings: Settings) -> tuple[AskEngine, DocumentReader]:
    path = settings.docingest_config
    if path is None:
        return OfflineAskEngine(), OfflineDocumentReader()
    if not path.is_file():
        raise SettingsError(f"docingest_config {path} is not a file")
    try:
        return open_docingest(path)
    except (OSError, ValueError) as e:  # an unreadable or invalid docingest settings file
        raise SettingsError(f"docingest_config {path} cannot be used: {e}") from e


def _fake_database() -> tuple[Database, Callable[[], None]]:
    """A throwaway database in a private temporary folder, removed when the process ends."""
    folder = Path(tempfile.mkdtemp(prefix="research-api-fake-"))
    database = Database.open(folder / "fake.sqlite3")
    database.create_all()

    def cleanup() -> None:
        database.close()
        shutil.rmtree(folder, ignore_errors=True)

    return database, cleanup


def build_container(
    settings: Settings,
    *,
    fake: bool = False,
    seed: Sequence[SeedUser] = (),
    cheap_hashing: bool = False,
    clock: Clock | None = None,
    fake_ask_pause_s: float = 0.0,
    ask_engine: AskEngine | None = None,
    document_reader: DocumentReader | None = None,
) -> Container:
    """Wire the service.

    Fake mode uses in-memory stand-ins and a throwaway database, accepts loopback only, and
    creates the accounts of ``seed``. Live mode opens the database named in the settings and
    refuses to start when its folders are open to other accounts or its tables are out of date.
    ``cheap_hashing`` makes password hashing fast enough for a large test suite, and ``clock``
    replaces the time source (to cross a session or rate-limit window); nothing else changes.
    In fake mode ``fake_ask_pause_s`` is the time the stand-in engine takes between the phases of
    an ask (so a screen shows them), and ``ask_engine`` and ``document_reader`` replace the
    stand-ins (tests).
    """
    if fake and not is_loopback(settings.bind_host):
        raise SettingsError("fake mode serves test data and may only bind a loopback address")
    # The asks are forgotten half an hour after they end, so they need a clock that moves, in
    # fake mode too (its fixed clock is for reproducible sessions and documents). A test can
    # still pass its own.
    ask_clock = clock or SystemClock()
    clock = clock or (FixedClock() if fake else SystemClock())
    mode: Mode = "fake" if fake else "live"
    api_version = version("research-api")
    engine: AskEngine
    reader: DocumentReader
    if fake:
        engine = ask_engine or FakeAskEngine(pause_s=fake_ask_pause_s)
        reader = document_reader or FakeDocumentReader()
        database, release = _fake_database()
    else:
        engine, reader = _live_ask_parts(settings)
        database = open_current_database(settings)
        release = database.close

    def cleanup() -> None:
        engine.close()
        release()

    auth = build_auth(database, settings, clock, ArgonPasswordHasher(cheap=cheap_hashing))
    for account in seed:
        auth.users.create(
            account.username,
            account.display_name,
            account.role,
            password=account.password,
            must_change_password=account.must_change_password,
            language=account.language,
        )
    caps = settings.circuit_caps.caps()
    # quantum-sim and docingest's gate lists do no I/O, so fake mode runs the real engine.
    circuits = CircuitsAdapter(timeout_s=caps.timeout_s)
    container = Container(
        mode=mode,
        settings=settings,
        version=api_version,
        clock=clock,
        report_status=ReportStatus(
            clock=clock, mode=mode, version=api_version, started_at=clock.now()
        ),
        parse_circuit=ParseCircuit(engine=circuits),
        run_circuit=RunCircuit(engine=circuits, caps=caps),
        check_circuit=CheckCircuit(engine=circuits, caps=caps),
        circuit_conventions=ShowCircuitConventions(engine=circuits),
        auth=auth,
        asks=AskDesk(engine=engine, clock=ask_clock),
        documents=DocumentServices(reader=reader),
        close=cleanup,
    )
    # A process that exits without a clean shutdown still removes the fake database's folder.
    weakref.finalize(container, cleanup)
    return container


def _fake_pause(text: str | None) -> float:
    if text is None:
        return DEFAULT_FAKE_ASK_PAUSE_S
    try:
        pause = float(text)
    except ValueError:
        pause = -1.0
    if not 0 <= pause <= MAX_FAKE_ASK_PAUSE_S:
        raise SettingsError(
            f"{FAKE_ASK_PAUSE_ENV} must be a number from 0 to {MAX_FAKE_ASK_PAUSE_S:g}"
        )
    return pause


def _with_web_dir(settings: Settings, env: Mapping[str, str]) -> Settings:
    """The settings, with ``web_dir`` taken from the environment when it is set there."""
    folder = env.get(WEB_DIR_ENV)
    if not folder:
        return settings
    try:
        return Settings.model_validate({**settings.model_dump(), "web_dir": folder})
    except ValidationError:
        raise SettingsError(f"{WEB_DIR_ENV} must be an absolute path") from None


def container_from_env(environ: Mapping[str, str] | None = None) -> Container:
    """The container for ``uvicorn --factory``: fake mode, or the settings file named in the env.

    ``RESEARCH_API_WEB_DIR`` names the built web app and wins over the ``web_dir`` setting, so
    fake mode can serve it too.
    """
    env = os.environ if environ is None else environ
    if env.get(FAKE_ENV) == "1":
        settings = _with_web_dir(Settings.defaults(), env)
        return build_container(
            settings,
            fake=True,
            seed=default_seed_users(),
            fake_ask_pause_s=_fake_pause(env.get(FAKE_ASK_PAUSE_ENV)),
        )
    path = env.get(CONFIG_ENV)
    if not path:
        raise SettingsError(f"set {CONFIG_ENV} to a settings file, or {FAKE_ENV}=1 for fake mode")
    return build_container(_with_web_dir(load_private_settings(Path(path)), env))


@dataclass(frozen=True)
class UserAdmin:
    """What the ``users`` command needs: the account use cases, and a way to let go."""

    users: ManageUsers
    close: Callable[[], None]


def open_user_admin(settings: Settings) -> UserAdmin:
    database = open_current_database(settings)
    auth = build_auth(database, settings, SystemClock(), ArgonPasswordHasher())
    return UserAdmin(users=auth.users, close=database.close)
