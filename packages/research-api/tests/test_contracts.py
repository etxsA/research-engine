"""The import contracts of pyproject.toml must fail on the imports they exist to stop.

Each case copies the package to a temporary folder, adds one import and runs import-linter
there. A contract that stopped working would pass `lint-imports` forever; these cases catch that.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
RUN = "from importlinter.cli import lint_imports_command; lint_imports_command()"

LAYERS = "Hexagonal layers: outer layers may import inner ones, never the reverse"
INDEPENDENT = "Adapters are independent of each other"
PURE = "Domain is pure: no framework, driver or sibling-package imports"
PORTS_ONLY = "Application and ports reach docingest, quantum-sim and qa-bench only through ports"
DOCINGEST = "Only the docingest adapters import docingest"
QUANTUM = "Only the circuits adapter imports quantum_sim"
QA_BENCH = "Only the bench adapter imports qa_bench"
HTTPX = "Only the models adapter imports httpx"
HASHING = "Only the hashing adapter imports pwdlib and argon2"
SQL = "Only the persistence adapter imports sqlalchemy and alembic"
PROTECTED = "Only the composition root and entrypoints wire concrete adapters"
PLUGIN = "research_api never imports a docingest plugin (docingest finds it by entry point)"

SRC = "src/research_api"

# The application imports the domain, so a forbidden import in the domain also breaks the
# application's contract through the chain; the cases below list both.

# (file, line added, contracts that must break). An empty set is a control: the import is allowed.
# A file that does not exist yet is created, which is how a new adapter is simulated.
CASES = {
    "domain imports an adapter": (
        "domain/status.py",
        "from ..adapters.clock import SystemClock",
        {LAYERS, PROTECTED},
    ),
    "ports import the application": (
        "ports/clock.py",
        "from ..application import status",
        {LAYERS},
    ),
    "domain imports a web framework": ("domain/status.py", "import fastapi", {PURE, PORTS_ONLY}),
    "domain imports sqlalchemy": ("domain/status.py", "import sqlalchemy", {PURE, PORTS_ONLY, SQL}),
    "domain imports docingest": (
        "domain/status.py",
        "import docingest",
        {PURE, PORTS_ONLY, DOCINGEST},
    ),
    "domain imports jsonschema": ("domain/status.py", "import jsonschema", {PURE, PORTS_ONLY}),
    "application imports httpx": ("application/status.py", "import httpx", {PORTS_ONLY, HTTPX}),
    "ports import quantum_sim": ("ports/clock.py", "import quantum_sim", {PORTS_ONLY, QUANTUM}),
    "application imports an adapter": (
        "application/status.py",
        "from ..adapters import fake",
        {LAYERS, PROTECTED},
    ),
    "the clock adapter imports docingest": (
        "adapters/clock/__init__.py",
        "import docingest",
        {DOCINGEST},
    ),
    "the persistence adapter imports docingest": (
        "adapters/persistence/__init__.py",
        "import docingest",
        {DOCINGEST},
    ),
    "the docingest adapter imports quantum_sim": (
        "adapters/docingest_ask/__init__.py",
        "import quantum_sim",
        {QUANTUM},
    ),
    "the circuits adapter imports qa_bench": (
        "adapters/circuits/__init__.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "the hashing adapter imports sqlalchemy": (
        "adapters/hashing/__init__.py",
        "import sqlalchemy",
        {SQL},
    ),
    "the files adapter imports alembic": ("adapters/files/__init__.py", "import alembic", {SQL}),
    "bootstrap imports docingest itself": (
        "bootstrap/container.py",
        "import docingest",
        {DOCINGEST},
    ),
    "an entrypoint imports qa_bench itself": ("entrypoints/cli.py", "import qa_bench", {QA_BENCH}),
    "one adapter imports another": (
        "adapters/bench/__init__.py",
        "from .. import files",
        {INDEPENDENT},
    ),
    "an adapter imports a plugin": (
        "adapters/circuits/__init__.py",
        "import docingest_index",
        {PLUGIN},
    ),
    "bootstrap imports a plugin": ("bootstrap/container.py", "import docingest_index", {PLUGIN}),
    "the package root imports docingest": ("__init__.py", "import docingest", {DOCINGEST}),
    "the adapters package imports sqlalchemy": (
        "adapters/__init__.py",
        "import sqlalchemy",
        {SQL},
    ),
    "a new adapter package imports docingest": (
        "adapters/auth/__init__.py",
        "import docingest",
        {DOCINGEST},
    ),
    "a new adapter module imports sqlalchemy": (
        "adapters/sessions.py",
        "import sqlalchemy",
        {SQL},
    ),
    "a new adapter module imports quantum_sim": (
        "adapters/sessions.py",
        "import quantum_sim",
        {QUANTUM},
    ),
    "a new adapter module imports qa_bench": (
        "adapters/reports.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "a module inside the docingest adapter imports quantum_sim": (
        "adapters/docingest_ask/parsing.py",
        "import quantum_sim",
        {QUANTUM},
    ),
    "the fake adapter imports the clock adapter": (
        "adapters/fake/__init__.py",
        "from .. import clock",
        {INDEPENDENT},
    ),
    "the circuit use cases import quantum_sim": (
        "application/circuits.py",
        "import quantum_sim",
        {PORTS_ONLY, QUANTUM},
    ),
    "the circuit rules import docingest": (
        "domain/circuits.py",
        "import docingest",
        {PURE, PORTS_ONLY, DOCINGEST},
    ),
    "the circuit port imports quantum_sim": (
        "ports/circuits.py",
        "import quantum_sim",
        {PORTS_ONLY, QUANTUM},
    ),
    "the circuits adapter imports another adapter": (
        "adapters/circuits/lane.py",
        "from .. import files",
        {INDEPENDENT},
    ),
    "control: the lane inside the circuits adapter imports quantum_sim": (
        "adapters/circuits/lane.py",
        "import quantum_sim\nimport docingest",
        set(),
    ),
    # The OpenQASM export: rules, port and use cases stay pure; only the circuits adapter reaches
    # docingest's export and quantum-sim's emitter.
    "the export use cases import docingest's export": (
        "application/circuits.py",
        "import docingest.application.qasm",
        {PORTS_ONLY, DOCINGEST},
    ),
    "the export use cases import the circuits adapter": (
        "application/circuits.py",
        "from ..adapters import circuits",
        {LAYERS, PROTECTED, PORTS_ONLY},  # and docingest and quantum_sim, through the chain
    ),
    "the export rules import quantum_sim's emitter": (
        "domain/circuits.py",
        "import quantum_sim.qasm",
        {PURE, PORTS_ONLY, QUANTUM},
    ),
    "the paper text rules import docingest": (
        "domain/documents.py",
        "import docingest.domain.circuits",
        {PURE, PORTS_ONLY, DOCINGEST},
    ),
    "the circuit port imports docingest": (
        "ports/circuits.py",
        "import docingest.domain.circuits",
        {PORTS_ONLY, DOCINGEST},
    ),
    "the paper reader port imports quantum_sim": (
        "ports/documents.py",
        "import quantum_sim.angles",
        {PORTS_ONLY, QUANTUM},
    ),
    "the circuit routes import quantum_sim's emitter themselves": (
        "entrypoints/http/circuits.py",
        "import quantum_sim.qasm",
        {QUANTUM},
    ),
    "the circuit routes import docingest themselves": (
        "entrypoints/http/circuits.py",
        "import docingest.application.qasm",
        {DOCINGEST},
    ),
    "the fake paper reader imports quantum_sim": (
        "adapters/fake/ask.py",
        "import quantum_sim.qasm",
        {QUANTUM},
    ),
    "the docingest paper reader imports quantum_sim": (
        "adapters/docingest_ask/documents.py",
        "import quantum_sim.qasm",
        {QUANTUM},
    ),
    "the circuits adapter imports the docingest paper reader": (
        "adapters/circuits/__init__.py",
        "from .. import docingest_ask",
        {INDEPENDENT},
    ),
    "the circuits adapter imports the fake paper reader": (
        "adapters/circuits/__init__.py",
        "from ..fake import ask",
        {INDEPENDENT},
    ),
    "a new module inside the circuits adapter imports qa_bench": (
        "adapters/circuits/qasm.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "control: the circuits adapter imports docingest's export and quantum_sim's emitter": (
        "adapters/circuits/__init__.py",
        "import docingest.adapters.qasm\nimport quantum_sim.qasm",
        set(),
    ),
    "control: a new module inside the circuits adapter imports docingest and quantum_sim": (
        "adapters/circuits/qasm.py",
        "import docingest.domain.circuits\nimport quantum_sim.angles",
        set(),
    ),
    "control: the export use cases import the paper reader port and the domain": (
        "application/circuits.py",
        "from ..ports import documents\nfrom ..domain import documents as rules",
        set(),
    ),
    "control: the circuit routes import the export use cases": (
        "entrypoints/http/circuits.py",
        "from ...application import circuits",
        set(),
    ),
    "domain imports pwdlib": ("domain/status.py", "import pwdlib", {PURE, PORTS_ONLY, HASHING}),
    "domain imports argon2": ("domain/status.py", "import argon2", {PURE, PORTS_ONLY, HASHING}),
    "the persistence adapter imports pwdlib": (
        "adapters/persistence/__init__.py",
        "import pwdlib",
        {HASHING},
    ),
    "the files adapter imports argon2": ("adapters/files/private.py", "import argon2", {HASHING}),
    "a new adapter module imports pwdlib": ("adapters/sessions.py", "import pwdlib", {HASHING}),
    "bootstrap imports argon2 itself": ("bootstrap/container.py", "import argon2", {HASHING}),
    "an entrypoint imports pwdlib itself": ("entrypoints/cli.py", "import pwdlib", {HASHING}),
    "a module inside the hashing adapter imports sqlalchemy": (
        "adapters/hashing/policy.py",
        "import sqlalchemy",
        {SQL},
    ),
    "application imports sqlalchemy": (
        "application/auth.py",
        "import sqlalchemy",
        {PORTS_ONLY, SQL},
    ),
    "ports import alembic": ("ports/auth.py", "import alembic", {PORTS_ONLY, SQL}),
    "application imports the hashing adapter": (
        "application/auth.py",
        "from ..adapters import hashing",
        {LAYERS, PROTECTED, PORTS_ONLY},  # and the hashing adapter's pwdlib, through the chain
    ),
    "the persistence adapter imports the hashing adapter": (
        "adapters/persistence/repos.py",
        "from .. import hashing",
        {INDEPENDENT},
    ),
    "the hashing adapter imports the files adapter": (
        "adapters/hashing/__init__.py",
        "from .. import files",
        {INDEPENDENT},
    ),
    "the files adapter imports the persistence adapter": (
        "adapters/files/private.py",
        "from .. import persistence",
        {INDEPENDENT},
    ),
    "bootstrap storage imports sqlalchemy itself": (
        "bootstrap/storage.py",
        "import sqlalchemy",
        {SQL},
    ),
    "the http auth routes import sqlalchemy": (
        "entrypoints/http/auth.py",
        "import sqlalchemy",
        {SQL},
    ),
    "the migrations import docingest": (
        "adapters/persistence/migrations/env.py",
        "import docingest",
        {DOCINGEST},
    ),
    "a migration imports quantum_sim": (
        "adapters/persistence/migrations/versions/0001_users_sessions_login_attempts.py",
        "import quantum_sim",
        {QUANTUM},
    ),
    "control: the migrations import alembic and sqlalchemy": (
        "adapters/persistence/migrations/env.py",
        "import alembic\nimport sqlalchemy",
        set(),
    ),
    "control: the hashing adapter imports pwdlib": (
        "adapters/hashing/__init__.py",
        "import pwdlib\nimport argon2",
        set(),
    ),
    "control: a module inside the hashing adapter imports argon2": (
        "adapters/hashing/policy.py",
        "import argon2",
        set(),
    ),
    "control: bootstrap storage imports two adapters": (
        "bootstrap/storage.py",
        "from ..adapters import files, persistence",
        set(),
    ),
    "control: a module inside the docingest adapter imports docingest": (
        "adapters/docingest_ask/parsing.py",
        "import docingest",
        set(),
    ),
    "control: a module inside persistence imports sqlalchemy": (
        "adapters/persistence/models.py",
        "import sqlalchemy",
        set(),
    ),
    "control: docingest in its adapter": (
        "adapters/docingest_ask/__init__.py",
        "import docingest",
        set(),
    ),
    "control: docingest in the circuits adapter": (
        "adapters/circuits/__init__.py",
        "import docingest",
        set(),
    ),
    "control: quantum_sim in the circuits adapter": (
        "adapters/circuits/__init__.py",
        "import quantum_sim",
        set(),
    ),
    "control: qa_bench in the bench adapter": (
        "adapters/bench/__init__.py",
        "import qa_bench",
        set(),
    ),
    "control: sqlalchemy in persistence": (
        "adapters/persistence/__init__.py",
        "import sqlalchemy\nimport alembic",
        set(),
    ),
    "control: bootstrap imports an adapter": (
        "bootstrap/container.py",
        "from ..adapters import persistence",
        set(),
    ),
    "control: an entrypoint imports bootstrap": (
        "entrypoints/cli.py",
        "from .. import bootstrap",
        set(),
    ),
    "the ask domain imports docingest": (
        "domain/ask.py",
        "import docingest",
        {PURE, PORTS_ONLY, DOCINGEST},
    ),
    "the document rules import httpx": (
        "domain/documents.py",
        "import httpx",
        {PURE, PORTS_ONLY, HTTPX},
    ),
    "the ask port imports docingest": (
        "ports/ask.py",
        "import docingest",
        {PORTS_ONLY, DOCINGEST},
    ),
    "the document port imports sqlalchemy": (
        "ports/documents.py",
        "import sqlalchemy",
        {PORTS_ONLY, SQL},
    ),
    "the ask use cases import fastapi": ("application/ask.py", "import fastapi", {PORTS_ONLY}),
    "the lane imports docingest": (
        "application/lanes.py",
        "import docingest",
        {PORTS_ONLY, DOCINGEST},
    ),
    "the document use cases import docingest": (
        "application/documents.py",
        "import docingest",
        {PORTS_ONLY, DOCINGEST},
    ),
    "the ask use cases import the fake adapter": (
        "application/ask.py",
        "from ..adapters import fake",
        {LAYERS, PROTECTED},
    ),
    "the fake adapter imports docingest": (
        "adapters/fake/ask.py",
        "import docingest",
        {DOCINGEST},
    ),
    "the fake corpus imports quantum_sim": (
        "adapters/fake/corpus.py",
        "import quantum_sim",
        {QUANTUM},
    ),
    "the fake adapter imports the docingest adapter": (
        "adapters/fake/ask.py",
        "from .. import docingest_ask",
        {INDEPENDENT},
    ),
    "the docingest adapter imports the fake adapter": (
        "adapters/docingest_ask/engine.py",
        "from .. import fake",
        {INDEPENDENT},
    ),
    "the paper reader imports sqlalchemy": (
        "adapters/docingest_ask/documents.py",
        "import sqlalchemy",
        {SQL},
    ),
    "an ask route imports docingest itself": (
        "entrypoints/http/asks.py",
        "import docingest",
        {DOCINGEST},
    ),
    "control: the docingest adapter imports its own modules and docingest": (
        "adapters/docingest_ask/engine.py",
        "import docingest.application.ask\nfrom . import errors",
        set(),
    ),
    "control: the fake adapter imports the domain and the ports": (
        "adapters/fake/ask.py",
        "from ...domain import ask\nfrom ...ports import ask as ports",
        set(),
    ),
    "the job rules import qa_bench": (
        "domain/jobs.py",
        "import qa_bench",
        {PURE, PORTS_ONLY, QA_BENCH},
    ),
    "the run rules import sqlalchemy": (
        "domain/runs.py",
        "import sqlalchemy",
        {PURE, PORTS_ONLY, SQL},
    ),
    "the job port imports httpx": ("ports/jobs.py", "import httpx", {PORTS_ONLY, HTTPX}),
    "the model port imports docingest": (
        "ports/models.py",
        "import docingest",
        {PORTS_ONLY, DOCINGEST},
    ),
    "the job desk imports httpx": ("application/jobs.py", "import httpx", {PORTS_ONLY, HTTPX}),
    "the job desk imports qa_bench": (
        "application/jobs.py",
        "import qa_bench",
        {PORTS_ONLY, QA_BENCH},
    ),
    "the live arms import docingest": (
        "application/arms.py",
        "import docingest",
        {PORTS_ONLY, DOCINGEST},
    ),
    "the result readers import sqlalchemy": (
        "application/results.py",
        "import sqlalchemy",
        {PORTS_ONLY, SQL},
    ),
    "the job desk imports an adapter": (
        "application/jobs.py",
        "from ..adapters import fake",
        {LAYERS, PROTECTED},
    ),
    "the live arms import the models adapter": (
        "application/arms.py",
        "from ..adapters import models",
        {LAYERS, PROTECTED, PORTS_ONLY},  # and httpx, through the chain
    ),
    "the models adapter imports docingest": (
        "adapters/models/openai.py",
        "import docingest",
        {DOCINGEST},
    ),
    "the models adapter imports qa_bench": (
        "adapters/models/openai.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "the models adapter imports the bench adapter": (
        "adapters/models/openai.py",
        "from .. import bench",
        {INDEPENDENT},
    ),
    "the run formats import httpx": ("adapters/bench/runs.py", "import httpx", {HTTPX}),
    "the scorer imports sqlalchemy": ("adapters/bench/scores.py", "import sqlalchemy", {SQL}),
    "the run formats import the files adapter": (
        "adapters/bench/runs.py",
        "from .. import files",
        {INDEPENDENT},
    ),
    "the bench file store imports qa_bench": (
        "adapters/files/bench.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "the job table imports httpx": ("adapters/persistence/jobs.py", "import httpx", {HTTPX}),
    "the job table imports the fake adapter": (
        "adapters/persistence/jobs.py",
        "from .. import fake",
        {INDEPENDENT},
    ),
    "the fake arms import httpx": ("adapters/fake/bench.py", "import httpx", {HTTPX}),
    "the fake arms import qa_bench": ("adapters/fake/bench.py", "import qa_bench", {QA_BENCH}),
    "the job routes import qa_bench itself": (
        "entrypoints/http/jobs.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "the job routes import httpx itself": ("entrypoints/http/jobs.py", "import httpx", {HTTPX}),
    "bootstrap imports httpx itself": ("bootstrap/container.py", "import httpx", {HTTPX}),
    "control: httpx in the models adapter": (
        "adapters/models/openai.py",
        "import httpx as http",
        set(),
    ),
    "control: qa_bench in the run formats": (
        "adapters/bench/runs.py",
        "import qa_bench.formats",
        set(),
    ),
    "control: bootstrap wires the models adapter": (
        "bootstrap/container.py",
        "from ..adapters import models",
        set(),
    ),
    "control: the job desk imports the domain and the ports": (
        "application/jobs.py",
        "from ..domain import runs\nfrom ..ports import models as ports",
        set(),
    ),
    "control: the job routes import the application": (
        "entrypoints/http/jobs.py",
        "from ...application import results",
        set(),
    ),
    "control: the ask use cases import the domain and the ports": (
        "application/ask.py",
        "from ..domain import documents\nfrom ..ports import documents as ports",
        set(),
    ),
    "domain sets imports qa_bench": (
        "domain/sets.py",
        "import qa_bench",
        {PURE, PORTS_ONLY, QA_BENCH},
    ),
    "domain sets imports jsonschema": ("domain/sets.py", "import jsonschema", {PURE, PORTS_ONLY}),
    "application sets imports qa_bench": (
        "application/sets.py",
        "import qa_bench",
        {PORTS_ONLY, QA_BENCH},
    ),
    "application sets imports the bench adapter": (
        "application/sets.py",
        "from ..adapters import bench",
        {LAYERS, PROTECTED, PORTS_ONLY},  # and qa_bench, through the chain
    ),
    "ports sets import jsonschema": ("ports/sets.py", "import jsonschema", {PORTS_ONLY}),
    "the set formats import sqlalchemy": ("adapters/bench/sets.py", "import sqlalchemy", {SQL}),
    "the set formats import docingest": ("adapters/bench/sets.py", "import docingest", {DOCINGEST}),
    "the set file store imports qa_bench": (
        "adapters/files/sets.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "the catalog imports qa_bench": (
        "adapters/persistence/repos.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "the set formats import the file store": (
        "adapters/bench/sets.py",
        "from .. import files",
        {INDEPENDENT},
    ),
    "the catalog imports the set formats": (
        "adapters/persistence/repos.py",
        "from .. import bench",
        {INDEPENDENT},
    ),
    "the http set routes import qa_bench": (
        "entrypoints/http/sets.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "the demo sets import qa_bench": ("bootstrap/demo_sets.py", "import qa_bench", {QA_BENCH}),
    "control: qa_bench in the set formats": ("adapters/bench/sets.py", "import qa_bench", set()),
    "control: bootstrap wires the set adapters": (
        "bootstrap/container.py",
        "from ..adapters import bench, files, persistence",
        set(),
    ),
    "control: the http set routes import the domain": (
        "entrypoints/http/sets.py",
        "from ...domain import sets",
        set(),
    ),
}


def lint(root: Path) -> tuple[int, str]:
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "NO_COLOR": "1"}
    done = subprocess.run(
        [sys.executable, "-c", RUN],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, " ".join((done.stdout + done.stderr).split())


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    shutil.copy(PACKAGE / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(PACKAGE / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def test_the_package_keeps_every_contract(sandbox: Path) -> None:
    code, out = lint(sandbox)
    assert code == 0, out
    assert " BROKEN" not in out


@pytest.mark.parametrize("case", CASES)
def test_a_contract_breaks_on_the_import_it_forbids(sandbox: Path, case: str) -> None:
    file, line, expected = CASES[case]
    target = sandbox / SRC / file
    target.parent.mkdir(parents=True, exist_ok=True)
    before = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(before + "\n" + line + "\n", encoding="utf-8")
    code, out = lint(sandbox)
    broken = {
        name
        for name in (
            LAYERS,
            INDEPENDENT,
            PURE,
            PORTS_ONLY,
            DOCINGEST,
            QUANTUM,
            QA_BENCH,
            HASHING,
            HTTPX,
            SQL,
            PROTECTED,
            PLUGIN,
        )
        if f"{name} BROKEN" in out
    }
    assert broken == expected, out
    assert (code != 0) == bool(expected)


def adapter_areas(adapters: Path) -> set[str]:
    """The folders and modules directly under ``adapters/``: each is one adapter area."""
    return {
        entry.stem if entry.is_file() else entry.name
        for entry in adapters.iterdir()
        if (entry.is_dir() and not entry.name.startswith(("__", ".")))
        or (entry.suffix == ".py" and entry.name != "__init__.py")
    }


def listed_as_independent() -> set[str]:
    pyproject = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    (contract,) = (
        c for c in pyproject["tool"]["importlinter"]["contracts"] if c["type"] == "independence"
    )
    prefix = "research_api.adapters."
    assert all(module.startswith(prefix) for module in contract["modules"])
    return {module.removeprefix(prefix) for module in contract["modules"]}


def test_every_adapter_area_is_listed_as_independent() -> None:
    """The independence contract is an explicit list, so a new area must be added to it."""
    present = adapter_areas(PACKAGE / SRC / "adapters")
    listed = listed_as_independent()
    assert present - listed == set(), "add to the independence contract in pyproject.toml"
    assert listed - present == set(), "listed in pyproject.toml but not in adapters/"


def test_a_new_adapter_area_is_noticed(tmp_path: Path) -> None:
    (tmp_path / "__init__.py").touch()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "persistence").mkdir()
    (tmp_path / "auth").mkdir()
    (tmp_path / "sessions.py").touch()
    assert adapter_areas(tmp_path) == {"persistence", "auth", "sessions"}
