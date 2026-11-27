"""HTTP request and response schemas. They are the wire contract that OpenAPI publishes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from ...application.ask import DeskStatus
from ...application.auth import SessionInfo
from ...application.jobs import BenchStatus
from ...domain.ask import (
    MAX_QUESTION_CHARS,
    AskAnswer,
    AskFailure,
    AskOptions,
    AskQuestion,
    AskRecord,
    AskState,
    KeywordMode,
    Phase,
)
from ...domain.auth import Language, Role, User
from ...domain.circuits import (
    MAX_VALUE_CHARS,
    MAX_VALUES,
    CheckTarget,
    PaperCircuitQasm,
    PaperCircuits,
    PaperQasm,
    PastedQasm,
    QasmFile,
)
from ...domain.documents import (
    DocumentDetail,
    DocumentList,
    DocumentPage,
    DocumentSummary,
    PageKind,
)
from ...domain.speech import Transcript
from ...domain.status import Mode, ServerStatus
from ...domain.synthesis import MAX_RAW_CHARS
from ...ports.ask import EngineState
from ...ports.circuits import GateListReading


class Health(BaseModel):
    ok: bool


class AskStatus(BaseModel):
    """The answer engine and the GPU lane. Nothing here names a person or a question."""

    state: EngineState = Field(
        description="`warming_up`: asks are refused with 503 for a few seconds. `ready`. "
        "`unavailable`: the last start or question found the index or the settings unusable "
        "(`reason` says which). Asks are still accepted, because the cause may have been fixed."
    )
    reason: str | None = Field(default=None, description="Fixed text; set when `unavailable`.")
    index_generation: str | None = Field(
        default=None, description="The index commit the last answer was made from."
    )
    queued: int = Field(description="Asks waiting for the GPU lane.")
    busy: bool = Field(description="True while the lane is answering a question.")

    @classmethod
    def from_domain(cls, desk: DeskStatus) -> AskStatus:
        return cls(
            state=desk.health.state,
            reason=desk.health.reason,
            index_generation=desk.health.index_generation,
            queued=desk.queued,
            busy=desk.busy,
        )


class BenchStatusOut(BaseModel):
    """The benchmark lanes. Nothing here names a person or a question."""

    running_job: str | None = Field(description="The run on the GPU lane now, by job id.")
    queued_runs: int = Field(description="Runs waiting for the GPU lane.")
    scoring: bool = Field(description="True while a scoring runs on the CPU lane.")
    queued_scores: int = Field(description="Scorings waiting for the CPU lane.")

    @classmethod
    def from_domain(cls, s: BenchStatus) -> BenchStatusOut:
        return cls(
            running_job=s.running_job,
            queued_runs=s.queued_runs,
            scoring=s.scoring,
            queued_scores=s.queued_scores,
        )


class SpeechStatus(BaseModel):
    """Speech recognition. Nothing here names a person or a clip."""

    stt: EngineState = Field(
        description="`ready` when the settings have a `[speech]` table, `unavailable` when they "
        "do not (transcriptions are then refused with 503). The server itself is not probed."
    )
    tts: EngineState = Field(
        description="`ready` when the settings have a `tts_endpoint`, `unavailable` when they do "
        "not (reading aloud is then refused with 503 and the web app uses the browser's voice). "
        "The server itself is not probed."
    )


class Status(BaseModel):
    mode: Mode = Field(description="`live`, or `fake` when the service runs on test data.")
    api_version: str
    started_at: datetime
    uptime_s: float
    ready: bool
    ask: AskStatus
    bench: BenchStatusOut
    speech: SpeechStatus

    @classmethod
    def from_domain(
        cls,
        status: ServerStatus,
        desk: DeskStatus,
        bench: BenchStatus,
        *,
        speech_ready: bool,
        tts_ready: bool,
    ) -> Status:
        return cls(
            mode=status.mode,
            api_version=status.version,
            started_at=status.started_at,
            uptime_s=status.uptime_s,
            ready=status.ready,
            ask=AskStatus.from_domain(desk),
            bench=BenchStatusOut.from_domain(bench),
            speech=SpeechStatus(
                stt="ready" if speech_ready else "unavailable",
                tts="ready" if tts_ready else "unavailable",
            ),
        )


class TranscriptionOut(BaseModel):
    text: str = Field(description="What was said. It is not kept: the caller edits it.")
    language: Language | None = Field(
        description="The language heard, or null when the speech server does not say."
    )
    duration_s: float = Field(description="Length of the clip in seconds.")

    @classmethod
    def from_domain(cls, transcript: Transcript) -> TranscriptionOut:
        return cls(
            text=transcript.text, language=transcript.language, duration_s=transcript.duration_s
        )


class ProblemField(BaseModel):
    path: str = Field(description="JSON pointer to the offending value.")
    message: str


class Problem(BaseModel):
    """RFC 9457 problem details (`application/problem+json`)."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    errors: list[ProblemField] | None = None
    error_id: str | None = Field(default=None, description="Only on 500: the id in the server log.")


class Request(BaseModel):
    """Base of the request bodies: a key the API does not know is an error, not ignored."""

    model_config = ConfigDict(extra="forbid")


class ParseRequest(Request):
    latex: str = Field(
        description="The body of one code block: a quantikz, Qcircuit, yquant or tikz-cd source."
    )


class GateListResponse(BaseModel):
    status: Literal["parsed", "partial", "unparsed"] = Field(
        description="`parsed` when every circuit was fully read, `unparsed` when none was."
    )
    text: str = Field(
        description="The gate list in words, as docingest gives it to the answer model."
    )
    circuits: list[dict[str, Any]] = Field(
        description="Each circuit as a `circuit/1` document. To simulate one, send it as text in "
        "`circuit_json`."
    )

    @classmethod
    def from_port(cls, reading: GateListReading) -> Self:
        return cls(
            status=reading.status,  # type: ignore[arg-type]
            text=reading.text,
            circuits=[dict(circuit) for circuit in reading.circuits],
        )


CIRCUIT_JSON = (
    "A `circuit/1` document as JSON text. It is text, not an object, so that a key written twice "
    "is seen and refused (answer 400) instead of the last one winning."
)
STATE_INPUT = (
    "The input: a basis label such as `010` (wire 1 first) or a list of amplitudes, each a "
    "number or a `[re, im]` pair. Default |0...0>."
)


def _values_are_short(values: dict[str, str | int | float]) -> dict[str, str | int | float]:
    for key, value in values.items():
        if len(key) > MAX_VALUE_CHARS:
            raise ValueError(f"a key is at most {MAX_VALUE_CHARS} characters long")
        if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
            raise ValueError(f"a value is at most {MAX_VALUE_CHARS} characters long")
    return values


Values = Annotated[
    dict[str, StrictStr | StrictInt | StrictFloat], AfterValidator(_values_are_short)
]
VALUES = (
    "A value for each symbol of the circuit, a number or an angle as text such as `\\pi/2` or "
    "`pi/2` (a `pi` that stands alone is `\\pi`). A key is one symbol: `theta`, `\\theta` or "
    "`\\theta_{1}`. "
    f"At most {MAX_VALUES} keys; a key or a value is at most {MAX_VALUE_CHARS} characters long."
)


class RunRequest(Request):
    circuit_json: str = Field(description=CIRCUIT_JSON)
    input: str | list[Any] | None = Field(default=None, description=STATE_INPUT)
    values: Values | None = Field(default=None, max_length=MAX_VALUES, description=VALUES)
    unitary: StrictBool = Field(
        default=False, description="Also list the unitary (up to 8 wires). `true` or `false` only."
    )


class CheckTargetBody(Request):
    """What to compare the circuit with. Give exactly one field."""

    state: str | list[Any] | None = Field(
        default=None, description="A state: a basis label or a list of amplitudes of norm 1."
    )
    unitary: list[list[Any]] | None = Field(
        default=None, description="A unitary matrix: rows of numbers or `[re, im]` pairs."
    )
    circuit_json: str | None = Field(
        default=None, description="A second circuit, as JSON text (see the first `circuit_json`)."
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        given = self.model_fields_set
        if len(given) != 1 or any(getattr(self, name) is None for name in given):
            raise ValueError("give exactly one of state, unitary and circuit_json, and not null")
        return self

    def to_domain(self) -> CheckTarget:
        return CheckTarget(state=self.state, unitary=self.unitary, circuit_json=self.circuit_json)


def _not_nullable(schema: dict[str, Any]) -> None:
    """Publish a field that may be left out, but not sent as null, without the null branch."""
    branches = [b for b in schema.pop("anyOf") if b != {"type": "null"}]
    schema.update(branches[0])


class CheckRequest(Request):
    circuit_json: str = Field(description=CIRCUIT_JSON)
    target: CheckTargetBody | None = Field(
        default=None,
        description="Leave it out to compare the two sides of an equality drawn in the circuit. "
        "Sending `null` is refused.",
        json_schema_extra=_not_nullable,
    )
    input: str | list[Any] | None = Field(default=None, description=STATE_INPUT)
    values: Values | None = Field(default=None, max_length=MAX_VALUES, description=VALUES)

    @field_validator("target", mode="before")
    @classmethod
    def _target_is_a_target_or_absent(cls, value: Any) -> Any:
        if value is None:
            raise ValueError(
                'target must be {"state": ...}, {"unitary": ...} or {"circuit_json": ...}'
            )
        return value


# --- OpenQASM 3 export ----------------------------------------------------------------------

QASM_VALUES = (
    VALUES + " A key that is not one symbol, or a value the export cannot use, is a 422 that "
    "points to `/values/<key>`, before any circuit is exported."
)


class QasmRequest(Request):
    circuit_json: str = Field(description=CIRCUIT_JSON)
    values: Values | None = Field(default=None, max_length=MAX_VALUES, description=QASM_VALUES)


class PaperQasmRequest(Request):
    values: Values | None = Field(
        default=None,
        max_length=MAX_VALUES,
        description=QASM_VALUES + " The same values go to every circuit of the paper, as "
        "`--set` does.",
    )


class QasmProgramOut(BaseModel):
    """One OpenQASM 3 program, ready to save under `filename`."""

    filename: str = Field(
        description="`circuit-pNN.qasm` for a pasted circuit; for a paper, the name "
        "`docingest circuits export` writes: `<doc_id[:16]>-bNN-cNN-pNN.qasm`."
    )
    part: int = Field(description="The side of a drawn equality, from 1.")
    sign: str | None = Field(
        description="The sign drawn before this part (`=`, `\\approx`, ...); null for part 1."
    )
    text: str = Field(description="The program: UTF-8 text with LF line ends, ending in a LF.")
    n_qubits: int
    n_clbits: int = Field(description="0 when the program measures nothing.")

    @classmethod
    def from_domain(cls, file: QasmFile) -> QasmProgramOut:
        p = file.program
        return cls(
            filename=file.filename,
            part=p.part,
            sign=p.sign,
            text=p.text,
            n_qubits=p.n_qubits,
            n_clbits=p.n_clbits,
        )


QASM_STATUS = (
    "`ok`, or `refused` with the reasons: a drawing with one part the export cannot express "
    "has no programs at all."
)
QASM_REASONS = "Why the circuit was refused, in the emitter's words; empty when `ok`."
QASM_NOTES = "The reader's notes about the drawing."
QASM_UNBOUND = (
    "For a refused circuit: the symbols with no value, as `values` keys (`theta_{12}`), listed "
    "only when values for them alone would export it."
)


class QasmResponse(BaseModel):
    """The export of a pasted circuit. A refused circuit is a 200 with `status: refused`."""

    status: Literal["ok", "refused"] = Field(description=QASM_STATUS)
    programs: list[QasmProgramOut] = Field(description="One program per part, in order.")
    reasons: list[str] = Field(description=QASM_REASONS)
    notes: list[str] = Field(description=QASM_NOTES)
    symbols: list[str] = Field(
        description="The free parameters the circuit mentions, by the reader's name "
        "(`theta_1` for `\\theta_{1}`), bound or not."
    )
    unmatched: list[str] = Field(
        description="The keys of `values`, as sent, that name no symbol of the circuit."
    )
    unbound: list[str] = Field(description=QASM_UNBOUND)

    @classmethod
    def from_domain(cls, done: PastedQasm) -> QasmResponse:
        export = done.export
        return cls(
            status=export.status,  # type: ignore[arg-type]
            programs=[QasmProgramOut.from_domain(f) for f in done.files],
            reasons=list(export.reasons),
            notes=list(export.notes),
            symbols=list(export.symbols),
            unmatched=list(export.unmatched),
            unbound=list(export.unbound),
        )


class PaperBlockOut(BaseModel):
    block: int = Field(
        description="The fenced `latex` block, from 1 over the whole paper, as docingest's "
        "export numbers it."
    )
    latex: str = Field(description="The body of the block.")
    status: Literal["parsed", "partial", "unparsed"] = Field(
        description="`parsed` when every circuit was fully read, `unparsed` when none was."
    )
    circuits: list[dict[str, Any]] = Field(
        description="Each circuit as a `circuit/1` document, numbered from 1 in the block."
    )


class PaperCircuitsResponse(BaseModel):
    """The circuit blocks of a paper. A paper with none has an empty `blocks`."""

    doc_id: str = Field(description="The full document id.")
    source_name: str
    blocks: list[PaperBlockOut]

    @classmethod
    def from_domain(cls, paper: PaperCircuits) -> PaperCircuitsResponse:
        return cls(
            doc_id=paper.doc_id,
            source_name=paper.source_name,
            blocks=[
                PaperBlockOut(
                    block=b.block,
                    latex=b.latex,
                    status=b.status,  # type: ignore[arg-type]
                    circuits=[dict(c) for c in b.circuits],
                )
                for b in paper.blocks
            ],
        )


class PaperCircuitQasmOut(BaseModel):
    block: int
    circuit: int = Field(description="The circuit in its block, from 1.")
    status: Literal["ok", "refused"] = Field(description=QASM_STATUS)
    programs: list[QasmProgramOut]
    reasons: list[str] = Field(description=QASM_REASONS)
    notes: list[str] = Field(description=QASM_NOTES)
    unbound: list[str] = Field(description=QASM_UNBOUND)

    @classmethod
    def from_domain(cls, done: PaperCircuitQasm) -> PaperCircuitQasmOut:
        export = done.export
        return cls(
            block=done.block,
            circuit=done.circuit,
            status=export.status,  # type: ignore[arg-type]
            programs=[QasmProgramOut.from_domain(f) for f in done.files],
            reasons=list(export.reasons),
            notes=list(export.notes),
            unbound=list(export.unbound),
        )


class PaperQasmResponse(BaseModel):
    """Every circuit of a paper, exported; nothing is written on the server."""

    doc_id: str = Field(description="The full document id.")
    source_name: str
    circuits: list[PaperCircuitQasmOut] = Field(
        description="Every circuit of every block, in order, refused ones included."
    )
    warnings: list[str] = Field(
        description="The same per-paper notes as `docingest circuits export --doc`: each circuit's "
        "notes after `<source_name>, block b, circuit c: `, `<source_name>: no circuits found`, "
        "and each `values` key that matches no symbol of any exported circuit. Notes about other "
        "papers of the store are not included, and a text that cannot be read is a 404."
    )

    @classmethod
    def from_domain(cls, done: PaperQasm) -> PaperQasmResponse:
        return cls(
            doc_id=done.doc_id,
            source_name=done.source_name,
            circuits=[PaperCircuitQasmOut.from_domain(c) for c in done.circuits],
            warnings=list(done.warnings),
        )


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current: str = Field(min_length=1, max_length=1024, description="The password in use now.")
    new: str = Field(
        max_length=1024,
        description=(
            "The new password: at least 12 characters, not the username, not a common password. "
            "Refusals come back as a 422 with a pointer to `/new`."
        ),
    )


class Me(BaseModel):
    username: str
    display_name: str
    role: Role
    must_change_password: bool = Field(
        description="True after an account is made or reset: every other endpoint answers 403 "
        "until the password is changed."
    )
    language: Language

    @classmethod
    def from_domain(cls, user: User) -> Me:
        return cls(
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            must_change_password=user.must_change_password,
            language=user.language,
        )


class SessionOut(BaseModel):
    """A signed-in browser. The cookie value is never shown."""

    id: str
    username: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool = Field(description="True for the session that made this request.")

    @classmethod
    def from_info(cls, info: SessionInfo) -> SessionOut:
        return cls(
            id=info.id,
            username=info.username,
            created_at=info.created_at,
            last_seen_at=info.last_seen_at,
            expires_at=info.expires_at,
            current=info.current,
        )


# --- asking ---------------------------------------------------------------------------------


class AskRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        description="The question, in any language. Sent in the body only: it is never put in a "
        "URL or a log.",
    )
    use_index: StrictBool = Field(
        default=True,
        description="False: the answer model searches the whole corpus itself (slow).",
    )
    bm25: KeywordMode | None = Field(
        default=None,
        description="When keyword matching joins the vector search: `english` (English questions "
        "and questions with LaTeX), `always` or `never`. Null: the server setting.",
    )
    whole_blocks: StrictBool | None = Field(
        default=None,
        description="Complete the equation, code block or table a chunk cuts. Null: the server "
        "setting.",
    )
    return_contexts: StrictBool = Field(
        default=True, description="Send the chunks the answer model read."
    )

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    def to_domain(self) -> AskQuestion:
        return AskQuestion(
            self.question.strip(),
            AskOptions(self.use_index, self.bm25, self.whole_blocks),
            self.return_contexts,
        )


class AskAccepted(BaseModel):
    ask_id: str
    position: int = Field(
        description="How many asks are ahead of this one, the running one included (0: next)."
    )


class CitationOut(BaseModel):
    key: str = Field(description="As printed in the answer: `<16 hex> pages 3-4`.")
    doc_id: str = Field(description="The full document id.")
    first_page: int
    last_page: int


class ContextOut(BaseModel):
    doc_id: str
    name: str
    start: int
    first_page: int
    last_page: int
    is_reference: bool
    text: str
    score: float | None = Field(
        default=None,
        description="The `qa-run/1` context field. Not filled yet: docingest's chunks carry no "
        "score, so it is always null. A client must not rely on it.",
    )


class TimingsOut(BaseModel):
    retrieve_s: float
    answer_s: float
    wall_s: float


class AskResponse(BaseModel):
    """The answer as a `qa-run/1` item holds it: `citations` and `contexts` have the same shape
    in both."""

    ask_id: str
    answer: str = Field(description="Markdown with LaTeX; the References list is not in it.")
    references_text: str
    citations: list[CitationOut]
    contexts: list[ContextOut]
    sources: list[str] = Field(description="Full ids of the papers the contexts come from.")
    warnings: list[str]
    index_generation: str | None
    timings: TimingsOut

    @classmethod
    def from_domain(cls, ask_id: str, answer: AskAnswer) -> AskResponse:
        return cls(
            ask_id=ask_id,
            answer=answer.answer,
            references_text=answer.references_text,
            citations=[
                CitationOut(
                    key=c.key, doc_id=c.doc_id, first_page=c.first_page, last_page=c.last_page
                )
                for c in answer.citations
            ],
            contexts=[
                ContextOut(
                    doc_id=c.doc_id,
                    name=c.name,
                    start=c.start,
                    first_page=c.first_page,
                    last_page=c.last_page,
                    is_reference=c.is_reference,
                    text=c.text,
                )
                for c in answer.contexts
            ],
            sources=list(answer.sources),
            warnings=list(answer.warnings),
            index_generation=answer.index_generation,
            timings=TimingsOut(
                retrieve_s=answer.timings.retrieve_s,
                answer_s=answer.timings.answer_s,
                wall_s=answer.timings.wall_s,
            ),
        )


class AskErrorOut(BaseModel):
    """Why no answer came. `status` is the HTTP status the same failure would have had."""

    ask_id: str
    kind: str = Field(description="The problem slug: `unavailable`, `upstream-error`, ...")
    status: int
    message: str
    retry_after_s: int | None = None

    @classmethod
    def from_domain(cls, ask_id: str, failure: AskFailure) -> AskErrorOut:
        return cls(
            ask_id=ask_id,
            kind=failure.kind,
            status=failure.status,
            message=failure.message,
            retry_after_s=failure.retry_after_s,
        )


class AskView(BaseModel):
    ask_id: str
    state: AskState
    position: int | None = Field(default=None, description="Set while `queued`.")
    phase: Phase | None = Field(default=None, description="Where a running ask is.")
    response: AskResponse | None = Field(default=None, description="Set when `done`.")
    error: AskErrorOut | None = Field(default=None, description="Set when `failed`.")

    @classmethod
    def from_domain(cls, record: AskRecord) -> AskView:
        answer, failure = record.answer, record.failure
        return cls(
            ask_id=record.ask_id,
            state=record.state,
            position=record.position,
            phase=record.phase if record.state is AskState.RUNNING else None,
            response=AskResponse.from_domain(record.ask_id, answer) if answer else None,
            error=AskErrorOut.from_domain(record.ask_id, failure) if failure else None,
        )


# --- papers ---------------------------------------------------------------------------------


class DocumentSummaryOut(BaseModel):
    doc_id: str
    short_id: str = Field(description="The first 16 characters, as a citation prints them.")
    title: str
    source_name: str
    source_kind: str = Field(description="`pdf`, `latex`, `office`, `image` or `text`.")
    pages: int
    page_kind: PageKind = Field(
        description="`section` for a LaTeX source (its page markers number sections), else `page`."
    )

    @classmethod
    def from_domain(cls, summary: DocumentSummary) -> DocumentSummaryOut:
        return cls(
            doc_id=summary.doc_id,
            short_id=summary.short_id,
            title=summary.title,
            source_name=summary.source_name,
            source_kind=summary.source_kind,
            pages=summary.pages,
            page_kind=summary.page_kind,
        )


class DocumentListOut(BaseModel):
    items: list[DocumentSummaryOut]
    total: int = Field(description="Papers that match, before the limit.")

    @classmethod
    def from_domain(cls, found: DocumentList) -> DocumentListOut:
        return cls(
            items=[DocumentSummaryOut.from_domain(s) for s in found.items], total=found.total
        )


class PageInfoOut(BaseModel):
    page: int
    method: str = Field(description="How the page text was made (`text_layer`, `latex`, ...).")
    title: str | None = Field(default=None, description="A section title (LaTeX sources).")


class DocumentOut(BaseModel):
    summary: DocumentSummaryOut
    authors: list[str]
    year: int | None
    doi: str | None
    arxiv_id: str | None
    url: str | None
    pages: list[PageInfoOut]

    @classmethod
    def from_domain(cls, detail: DocumentDetail) -> DocumentOut:
        return cls(
            summary=DocumentSummaryOut.from_domain(detail.summary),
            authors=list(detail.authors),
            year=detail.year,
            doi=detail.doi,
            arxiv_id=detail.arxiv_id,
            url=detail.url,
            pages=[PageInfoOut(page=p.page, method=p.method, title=p.title) for p in detail.pages],
        )


class DocumentPageOut(BaseModel):
    doc_id: str
    page: int
    pages: int
    kind: PageKind
    title: str | None = None
    markdown: str = Field(description="The page as Markdown with LaTeX math.")

    @classmethod
    def from_domain(cls, page: DocumentPage) -> DocumentPageOut:
        return cls(
            doc_id=page.doc_id,
            page=page.page,
            pages=page.pages,
            kind=page.kind,
            title=page.title,
            markdown=page.markdown,
        )


class SynthesisRequest(Request):
    text: StrictStr = Field(
        max_length=MAX_RAW_CHARS,
        description="A piece of an answer as written. Citations, markdown and formulas are "
        'cleaned on the server (a formula is said as "ecuación" or "equation"); what is '
        "left must be 1 to `max_tts_chars` characters (400 by default).",
    )
    language: Language | None = Field(
        default=None,
        description="`es` or `en`. Without it the language is guessed from the text.",
    )
