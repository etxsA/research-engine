"""Settings: the TOML file that configures the service, and the rules its values must meet."""

from __future__ import annotations

import ipaddress
import re
import tomllib
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from ..domain.circuits import (
    DEFAULT_MAX_BODY_BYTES,
    DEFAULT_MAX_OPS,
    DEFAULT_TIMEOUT_S,
    CircuitCaps,
)

# Tailscale hands out addresses from the CGNAT block (IPv4) and a unique-local block (IPv6).
_TAILSCALE_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)


_DEFAULT_PORT = {"http": 80, "https": 443}
_ORIGIN = re.compile(
    r"(?P<scheme>[A-Za-z]+)://(?P<host>\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::(?P<port>[0-9]{1,5}))?/?"
)
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_ORIGIN_FORM = "must look like http://host:port or https://host"


class SettingsError(ValueError):
    """The settings file is missing, unreadable, not TOML or breaks a rule."""


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The IP address a string names, or None. ``::ffff:a.b.c.d`` counts as the IPv4 address."""
    try:
        address = ipaddress.ip_address(host.partition("%")[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_loopback(host: str) -> bool:
    address = _address(host)
    return address is not None and address.is_loopback


def is_ip_address(host: str) -> bool:
    return _address(host) is not None


def same_address(first: str, second: str) -> bool:
    """Whether two strings name the same IP address (``::ffff:a.b.c.d`` equals ``a.b.c.d``)."""
    address = _address(first)
    return address is not None and address == _address(second)


def is_tailscale(host: str) -> bool:
    address = _address(host)
    return address is not None and any(
        address in network for network in _TAILSCALE_NETWORKS if address.version == network.version
    )


def _origin_text(scheme: str, host: str, port: int) -> str:
    """An origin as a browser sends it in ``Origin``: default port left out."""
    return f"{scheme}://{host}" + ("" if port == _DEFAULT_PORT[scheme] else f":{port}")


def normalize_origin(value: str) -> str:
    """The ``Origin`` header a browser would send for this origin, or a ``ValueError``.

    The scheme and host are lowercased, an IPv6 host is written in its short form, the port loses
    its leading zeros and is dropped when it is the default one. Anything a browser could not
    send (an empty or zero port, a space, ``;``, a trailing dot, a numeric host that is not a
    plain IPv4 address) is refused.
    """
    parts = urlsplit(value)
    if parts.path not in ("", "/") or "?" in value or "#" in value or "@" in parts.netloc:
        raise ValueError("must be an origin only: no path, query, fragment or credentials")
    found = _ORIGIN.fullmatch(value)
    if found is None or found["scheme"].lower() not in _DEFAULT_PORT:
        raise ValueError(_ORIGIN_FORM)
    scheme = found["scheme"].lower()
    host = found["host"].lower()
    port = int(found["port"]) if found["port"] else _DEFAULT_PORT[scheme]
    if not 1 <= port <= 65535:
        raise ValueError("must have a port from 1 to 65535")
    try:
        if host.startswith("["):
            host = f"[{ipaddress.IPv6Address(host[1:-1])}]"
        elif re.fullmatch(r"[0-9.]+", host):
            host = str(ipaddress.IPv4Address(host))
    except ValueError:
        raise ValueError(_ORIGIN_FORM) from None
    if not host.startswith("[") and (
        len(host) > 253 or not all(_LABEL.fullmatch(label) for label in host.split("."))
    ):
        raise ValueError(_ORIGIN_FORM)
    return _origin_text(scheme, host, port)


class CircuitCapsSettings(BaseModel):
    """The ``[circuit_caps]`` table. The upper bounds are quantum-sim's own limits (32 Mi
    characters of input, 10,000 operations) and a timeout of ten minutes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_body_bytes: StrictInt = Field(default=DEFAULT_MAX_BODY_BYTES, ge=1, le=32 * 1024 * 1024)
    max_ops: StrictInt = Field(default=DEFAULT_MAX_OPS, ge=1, le=10_000)
    timeout_s: StrictInt | StrictFloat = Field(default=DEFAULT_TIMEOUT_S, gt=0, le=600)

    def caps(self) -> CircuitCaps:
        return CircuitCaps(self.max_body_bytes, self.max_ops, float(self.timeout_s))


class Settings(BaseModel):
    """Every key is optional; a mistyped one is an error (``extra="forbid"``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bind_host: str = "127.0.0.1"
    port: StrictInt = Field(default=8090, ge=1, le=65535)
    public_origin: str | None = None
    circuit_caps: CircuitCapsSettings = CircuitCapsSettings()
    # Where the service keeps its files. Both are needed to run for real; fake mode uses a
    # throwaway folder. Absolute paths (a leading ~ is expanded).
    data_dir: Path | None = None
    db_path: Path | None = None
    log_dir: Path | None = None
    # The docingest settings file that names the corpus, the index and the model servers. Without
    # it everything but asking works. Absolute path; a change needs a restart.
    docingest_config: Path | None = None
    # The built web app (apps/web/dist). When set, the API serves it from its own origin.
    web_dir: Path | None = None
    session_idle_hours: StrictInt = Field(default=12, ge=1, le=720)
    session_max_days: StrictInt = Field(default=7, ge=1, le=90)
    login_max_failures: StrictInt = Field(default=5, ge=1, le=100)
    login_window_minutes: StrictInt = Field(default=15, ge=1, le=1440)

    @field_validator("bind_host")
    @classmethod
    def _bind_host_is_private(cls, value: str) -> str:
        if not (is_loopback(value) or is_tailscale(value)):
            raise ValueError(
                "must be a loopback address or a Tailscale address (never 0.0.0.0 or a LAN address)"
            )
        return value

    @field_validator("public_origin")
    @classmethod
    def _origin_is_an_origin(cls, value: str | None) -> str | None:
        return None if value is None else normalize_origin(value)

    @field_validator("data_dir", "db_path", "log_dir", "docingest_config", "web_dir")
    @classmethod
    def _path_is_absolute(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        value = value.expanduser()
        if not value.is_absolute():
            raise ValueError("must be an absolute path")
        return value

    @model_validator(mode="after")
    def _idle_is_within_the_session(self) -> Self:
        if self.session_idle_hours > self.session_max_days * 24:
            raise ValueError("session_idle_hours cannot be longer than session_max_days")
        return self

    @property
    def origin(self) -> str:
        """The origin browsers use, which a write request must match."""
        if self.public_origin is not None:
            return self.public_origin
        address = _address(self.bind_host)
        assert address is not None  # the validator accepts loopback and Tailscale addresses only
        host = f"[{address}]" if address.version == 6 else str(address)
        return _origin_text("http", host, self.port)

    @classmethod
    def defaults(cls) -> Self:
        return cls()


def load_settings(path: Path) -> Settings:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise SettingsError(f"cannot read settings file {path}: {e.strerror or e}") from e
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise SettingsError(f"settings file {path} is not valid TOML: {e}") from e
    try:
        return Settings.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or 'settings'}: {err['msg']}"
            for err in e.errors(include_url=False, include_input=False)
        )
        raise SettingsError(f"settings file {path} is not valid: {problems}") from None
