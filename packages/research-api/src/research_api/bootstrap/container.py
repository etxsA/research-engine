"""Composition root: builds the use cases and wires the adapters to their ports."""

from __future__ import annotations

import contextlib
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

from ..adapters.bench import QaBenchRunFormats, QaBenchScorer, QaBenchSetFormats
from ..adapters.circuits import CircuitsAdapter
from ..adapters.clock import SystemClock
from ..adapters.docingest_ask import (
    OfflineAskEngine,
    OfflineDocumentReader,
    open_docingest,
)
from ..adapters.fake import (
    FakeArms,
    FakeAskEngine,
    FakeDocumentReader,
    FakeModelServer,
    FakeSynthesizer,
    FakeTranscriber,
    FixedClock,
)
from ..adapters.files import BenchFileStore, SetFileStore
from ..adapters.hashing import ArgonPasswordHasher
from ..adapters.models import OpenAiModels, OpenAiSynthesizer, OpenAiTranscriber
from ..adapters.persistence import Database
from ..application.arms import LiveArms
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
from ..application.jobs import RECHECK_S, JobDesk
from ..application.lanes import Lane
from ..application.results import RunsDesk, ScoresDesk
from ..application.sets import SetServices, build_set_services
from ..application.speech import SpeechDesk
from ..application.status import ReportStatus
from ..application.synthesis import SynthesisDesk
from ..domain.auth import LoginPolicy, SessionPolicy
from ..domain.speech import SpeechLimits
from ..domain.status import Mode
from ..domain.synthesis import SynthesisLimits, Voices
from ..ports.ask import AskEngine
from ..ports.auth import PasswordHasher
from ..ports.clock import Clock
from ..ports.documents import DocumentReader
from ..ports.jobs import Arms
from ..ports.models import ModelEntry, ModelServer
from ..ports.speech import Synthesizer, Transcriber
from .demo_sets import seed_demo_sets
from .seed import SeedUser, default_seed_users
from .settings import Settings, SettingsError, is_loopback
from .storage import load_private_settings, open_current_database, read_secret, storage_paths

CONFIG_ENV = "RESEARCH_API_CONFIG"
FAKE_ENV = "RESEARCH_API_FAKE"
FAKE_ASK_PAUSE_ENV = "RESEARCH_API_FAKE_ASK_PAUSE"
DEFAULT_FAKE_ASK_PAUSE_S = 0.6
MAX_FAKE_ASK_PAUSE_S = 10.0
FAKE_BENCH_PAUSE_ENV = "RESEARCH_API_FAKE_BENCH_PAUSE"
DEFAULT_FAKE_BENCH_PAUSE_S = 0.25
MAX_FAKE_BENCH_PAUSE_S = 10.0
WEB_DIR_ENV = "RESEARCH_API_WEB_DIR"


@dataclass(frozen=True)
class BenchServices:
    """The benchmark: the job desk (runs and scorings), the readers of runs and scores, and the
    model servers the runs may ask."""

    jobs: JobDesk
    runs: RunsDesk
    scores: ScoresDesk
    models: ModelServer


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
    sets: SetServices
    bench: BenchServices
    speech: SpeechDesk | None
    """None when the settings have no speech-to-text server: the route answers 503."""
    synthesis: SynthesisDesk | None
    """None when the settings have no ``tts_endpoint``: the route answers 503."""
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


def _release_quietly(*releases: Callable[[], None]) -> None:
    """Run the releases in the order given, each one even if an earlier one fails.

    For a start that failed: the caller raises the error that made it necessary, and an error
    from a release must not take its place.
    """
    for release in releases:
        with contextlib.suppress(Exception):
            release()


def _fake_storage() -> tuple[Database, Path, Callable[[], None]]:
    """A throwaway database and data folder in a private temporary folder, removed at the end."""
    folder = Path(tempfile.mkdtemp(prefix="research-api-fake-"))
    try:
        database = Database.open(folder / "fake.sqlite3")
        try:
            database.create_all()
        except Exception:
            _release_quietly(database.close)
            raise
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise

    def cleanup() -> None:
        try:
            database.close()
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    return database, folder / "data", cleanup


def build_sets(database: Database, data_dir: Path, clock: Clock) -> SetServices:
    return build_set_services(
        catalog=database.sets,
        files=SetFileStore(data_dir / "sets"),
        formats=QaBenchSetFormats(),
        clock=clock,
    )


def build_bench(
    *,
    database: Database,
    data_dir: Path,
    sets: SetServices,
    arms: Arms,
    engine: AskEngine,
    models: ModelServer,
    clock: Clock,
    lane: Lane,
    api_version: str,
    recheck_s: float | None = None,
) -> BenchServices:
    files = BenchFileStore(data_dir)
    formats = QaBenchRunFormats()
    scorer = QaBenchScorer()
    set_formats = QaBenchSetFormats()
    return BenchServices(
        jobs=JobDesk(
            repo=database.jobs,
            files=files,
            formats=formats,
            scorer=scorer,
            sets=sets.manage,
            set_formats=set_formats,
            arms=arms,
            engine=engine,
            models=models,
            clock=clock,
            gpu=lane,
            runner=f"research-api {api_version}",
            recheck_s=RECHECK_S if recheck_s is None else recheck_s,
        ),
        runs=RunsDesk(
            files=files,
            formats=formats,
            repo=database.jobs,
            sets=sets.manage,
            set_formats=set_formats,
        ),
        scores=ScoresDesk(files=files, scorer=scorer, repo=database.jobs),
        models=models,
    )


def _model_server(settings: Settings) -> ModelServer:
    return OpenAiModels(
        [ModelEntry(m.name, m.endpoint, m.label or m.name, m.revision) for m in settings.models]
    )


def _speech_desk(
    settings: Settings, *, fake: bool, transcriber: Transcriber | None
) -> SpeechDesk | None:
    section = settings.speech
    if transcriber is None:
        if fake:
            transcriber = FakeTranscriber()
        elif section is None or section.stt_endpoint is None or section.stt_api_key_file is None:
            return None
        else:
            transcriber = OpenAiTranscriber(
                section.stt_endpoint,
                section.stt_model,
                read_secret(section.stt_api_key_file, what="the speech server key file"),
                timeout_s=float(section.timeout_s),
            )
    limits = section.limits() if section is not None else SpeechLimits()
    return SpeechDesk(transcriber=transcriber, limits=limits)


def _synthesis_desk(
    settings: Settings, *, fake: bool, synthesizer: Synthesizer | None
) -> SynthesisDesk | None:
    section = settings.speech
    if synthesizer is None:
        if fake:
            synthesizer = FakeSynthesizer()
        elif section is None or section.tts_endpoint is None or section.tts_api_key_file is None:
            return None
        else:
            synthesizer = OpenAiSynthesizer(
                section.tts_endpoint,
                section.tts_model,
                read_secret(section.tts_api_key_file, what="the reading server key file"),
                timeout_s=float(section.tts_timeout_s),
            )
    limits = section.synthesis_limits() if section is not None else SynthesisLimits()
    voices = section.voices() if section is not None else Voices()
    return SynthesisDesk(synthesizer=synthesizer, limits=limits, voices=voices)


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
    fake_bench_pause_s: float = 0.0,
    bench_arms: Arms | None = None,
    model_server: ModelServer | None = None,
    bench_recheck_s: float | None = None,
    transcriber: Transcriber | None = None,
    synthesizer: Synthesizer | None = None,
) -> Container:
    """Wire the service.

    Fake mode uses in-memory stand-ins and a throwaway database, accepts loopback only, and
    creates the accounts of ``seed``. Live mode opens the database named in the settings and
    refuses to start when its folders are open to other accounts or its tables are out of date.
    ``cheap_hashing`` makes password hashing fast enough for a large test suite, and ``clock``
    replaces the time source (to cross a session or rate-limit window); nothing else changes.
    In fake mode ``fake_ask_pause_s`` is the time the stand-in engine takes between the phases of
    an ask (so a screen shows them), and ``ask_engine`` and ``document_reader`` replace the
    stand-ins (tests). ``fake_bench_pause_s`` is the time a fake benchmark answer takes;
    ``bench_arms``, ``model_server`` and ``bench_recheck_s`` (how often a run waiting for a model
    server looks again) replace the stand-ins and the default for tests. ``transcriber`` replaces
    the speech server (fake mode has a stand-in; live mode has none without ``[speech]``), and
    ``synthesizer`` the server that reads answers aloud in the same way.
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
    else:
        engine, reader = _live_ask_parts(settings)
    try:
        if fake:
            database, data_dir, release = _fake_storage()
        else:
            data_dir = storage_paths(settings)[0]
            database = open_current_database(settings)
            release = database.close
    except Exception:
        _release_quietly(engine.close)  # the storage failed to open: the engine is all there is
        raise

    def cleanup() -> None:
        engine.close()
        release()

    try:
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
        sets = build_sets(database, data_dir, clock)
        if fake:
            seed_demo_sets(sets)
        models = model_server or (FakeModelServer() if fake else _model_server(settings))
        arms: Arms
        if bench_arms is not None:
            arms = bench_arms
        elif fake:
            arms = FakeArms(pause_s=fake_bench_pause_s)
        else:
            arms = LiveArms(engine=engine, models=models)
        asks = AskDesk(engine=engine, clock=ask_clock)
        bench = build_bench(
            database=database,
            data_dir=data_dir,
            sets=sets,
            arms=arms,
            engine=engine,
            models=models,
            clock=ask_clock,
            lane=asks.lane,
            api_version=api_version,
            recheck_s=bench_recheck_s,
        )
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
            asks=asks,
            documents=DocumentServices(reader=reader),
            sets=sets,
            bench=bench,
            speech=_speech_desk(settings, fake=fake, transcriber=transcriber),
            synthesis=_synthesis_desk(settings, fake=fake, synthesizer=synthesizer),
            close=cleanup,
        )
        # A process that exits without a clean shutdown still removes the fake database's folder.
        weakref.finalize(container, cleanup)
        return container
    except Exception:
        # A failed start must not leave the throwaway folder, the open database or the engine.
        _release_quietly(release, engine.close)
        raise


def _fake_pause(text: str | None, *, name: str, default: float, limit: float) -> float:
    if text is None:
        return default
    try:
        pause = float(text)
    except ValueError:
        pause = -1.0
    if not 0 <= pause <= limit:
        raise SettingsError(f"{name} must be a number from 0 to {limit:g}")
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
            fake_ask_pause_s=_fake_pause(
                env.get(FAKE_ASK_PAUSE_ENV),
                name=FAKE_ASK_PAUSE_ENV,
                default=DEFAULT_FAKE_ASK_PAUSE_S,
                limit=MAX_FAKE_ASK_PAUSE_S,
            ),
            fake_bench_pause_s=_fake_pause(
                env.get(FAKE_BENCH_PAUSE_ENV),
                name=FAKE_BENCH_PAUSE_ENV,
                default=DEFAULT_FAKE_BENCH_PAUSE_S,
                limit=MAX_FAKE_BENCH_PAUSE_S,
            ),
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
