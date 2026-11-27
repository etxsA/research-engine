"""Routes of the circuit check: read LaTeX, simulate, compare, export as OpenQASM 3.

The answers of run and check are quantum-sim's own ``quantum-sim-result/1`` documents, passed
through as JSON. A circuit the simulator refuses is a 200 with ``status: refused`` and the
reasons, as the command's exit status 1; it is not an HTTP error.

The OpenQASM 3 export answers typed documents, and a refused circuit is a 200 too. A paper's
circuits are listed and exported under ``/documents/{doc_id}`` (a second router, with the same
route class), read only: the programs come back to the caller and nothing is written on the
server. Neither a circuit nor a value is logged; the access log has the route template only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from ...bootstrap import Container
from ...domain.circuits import USE_THE_CLI, read_json_strict
from ...domain.errors import TooLargeError
from .asks import DocId
from .schemas import (
    CheckRequest,
    GateListResponse,
    PaperCircuitsResponse,
    PaperQasmRequest,
    PaperQasmResponse,
    ParseRequest,
    Problem,
    QasmRequest,
    QasmResponse,
    RunRequest,
)
from .security import SESSION_COOKIE, member, signed_in

SIMULATOR_NOTE = (
    "The body is quantum-sim's `quantum-sim-result/1` document as it is: `status` (`ok`, "
    "`symbolic` or `refused`), `conventions`, `input`, `circuits`, `comparisons`, `notes` and "
    "`refused`. A circuit the simulator refuses is a 200 with `status: refused`."
)


class BoundedRequest(Request):
    """A request whose body is read with a size cap, stopping as soon as it is over."""

    limit = 0

    async def body(self) -> bytes:
        if not hasattr(self, "_body"):
            declared = self.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > self.limit:
                raise self._too_large()
            chunks: list[bytes] = []
            total = 0
            async for chunk in self.stream():
                total += len(chunk)
                if total > self.limit:
                    raise self._too_large()
                chunks.append(chunk)
            self._body = b"".join(chunks)
        return self._body

    def _too_large(self) -> TooLargeError:
        return TooLargeError(f"The request body is over {self.limit} bytes. {USE_THE_CLI}")


def check_caller(request: Request) -> None:
    member(signed_in(request, request.cookies.get(SESSION_COOKIE)))


class CircuitRoute(APIRoute):
    """Checks the caller, caps the body before FastAPI reads it and refuses a key written twice.

    The caller is checked first, so a request with no session is a 401 whatever its body holds and
    costs no body read. The router's ``member`` dependency finds the caller already resolved on
    the request, so the session is read once, and puts the session cookie in the OpenAPI
    contract.

    FastAPI would parse the body with ``json.loads``, which keeps the last value of a repeated
    key without a word. Reading the raw bytes first makes the refusal come before the model.
    """

    def get_route_handler(self) -> Callable[[Request], Any]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            container: Container = request.app.state.container
            await run_in_threadpool(check_caller, request)
            bounded = BoundedRequest(request.scope, request.receive)
            bounded.limit = container.settings.circuit_caps.max_body_bytes
            body = await bounded.body()
            if body:
                read_json_strict(body, what="The request body")
            return await original(bounded)

        return handler


router = APIRouter(
    prefix="/circuits",
    tags=["circuits"],
    route_class=CircuitRoute,
    dependencies=[Depends(member)],
    responses={"default": {"model": Problem, "description": "A problem."}},
)
# A paper's circuits: the same checks, under the paper's path.
papers_router = APIRouter(
    prefix="/documents",
    tags=["circuits"],
    route_class=CircuitRoute,
    dependencies=[Depends(member)],
    responses={"default": {"model": Problem, "description": "A problem."}},
)

_SIZE_PROBLEMS: dict[int | str, dict[str, Any]] = {
    400: {
        "model": Problem,
        "description": "The body is not valid JSON, or a JSON key is written twice (in the body "
        "or in a `circuit_json`).",
    },
    413: {"model": Problem, "description": "The body is over the cap."},
    422: {
        "model": Problem,
        "description": "The request is not valid, or the circuit is over the synchronous caps "
        "(wires, operations, time); `detail` points to the `quantum-sim` command.",
    },
    503: {"model": Problem, "description": "Circuit jobs are all busy; see `Retry-After`."},
}


_PAPER_PROBLEMS: dict[int | str, dict[str, Any]] = {
    **_SIZE_PROBLEMS,
    404: {"model": Problem, "description": "No such document, or its text cannot be read."},
    409: {"model": Problem, "description": "The id prefix names more than one paper."},
    422: {
        "model": Problem,
        "description": "The request is not valid, or the paper's circuits are over the "
        "operation cap or the time; `detail` points to `docingest circuits export --doc`.",
    },
}


def _container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


@router.post(
    "/parse",
    operation_id="parseCircuit",
    summary="Read the circuits drawn in LaTeX",
    description="Reads quantikz, Qcircuit, yquant and tikz-cd source with docingest and returns "
    "the gate list in words and each circuit as a `circuit/1` document.",
    responses=_SIZE_PROBLEMS,
)
async def parse_circuit(request: Request, body: ParseRequest) -> GateListResponse:
    reading = await _container(request).parse_circuit(body.latex)
    return GateListResponse.from_port(reading)


@router.post(
    "/run",
    operation_id="runCircuit",
    summary="Simulate a circuit",
    description="The state and the outcome probabilities of a circuit, and its unitary on "
    "request. Up to 12 wires, or 8 with the unitary, and up to the configured number of "
    "operations; a bigger one is a 422 that points to the `quantum-sim` command. " + SIMULATOR_NOTE,
    responses=_SIZE_PROBLEMS,
)
async def run_circuit(request: Request, body: RunRequest) -> dict[str, Any]:
    result = await _container(request).run_circuit(
        circuit_json=body.circuit_json,
        input_state=body.input,
        values=body.values,
        unitary=body.unitary,
    )
    return dict(result)


@router.post(
    "/check",
    operation_id="checkCircuit",
    summary="Compare a circuit with a state, a unitary or another circuit",
    description="Whether the circuit equals the target up to global phase. With no target, the "
    "two sides of an equality drawn in the circuit are compared. Up to 12 wires against a "
    "state and 8 for every comparison that builds a unitary. " + SIMULATOR_NOTE,
    responses=_SIZE_PROBLEMS,
)
async def check_circuit(request: Request, body: CheckRequest) -> dict[str, Any]:
    result = await _container(request).check_circuit(
        circuit_json=body.circuit_json,
        target=body.target.to_domain() if body.target else None,
        input_state=body.input,
        values=body.values,
    )
    return dict(result)


@router.get(
    "/conventions",
    operation_id="getCircuitConventions",
    summary="The conventions, the gate table and the limits of the simulator",
    description="What `quantum-sim conventions` prints: the conventions every result states, the "
    "gate table and quantum-sim's own limits (the limits of this API are smaller and are named "
    "in its 422 answers).",
)
async def circuit_conventions(request: Request) -> dict[str, Any]:
    return dict(_container(request).circuit_conventions())


QASM_NOTE = (
    "Each program is OpenQASM 3 with `stdgates.inc` only: wire `i` (1 at the top) is `q[i-1]` "
    "and a measurement writes `c[i-1]`. A drawn equality gives one program per side. A circuit "
    "the export cannot express is a 200 with `status: refused` and the reasons, word for word. "
    "No wire cap; the operation cap and the time limit apply."
)


@router.post(
    "/qasm",
    operation_id="exportQasm",
    summary="Export a circuit as OpenQASM 3",
    description="The OpenQASM 3 program of each part of a `circuit/1` document, named "
    "`circuit-pNN.qasm`, with `research-api` in the generated-by line and no source line. "
    + QASM_NOTE,
    responses=_SIZE_PROBLEMS,
)
async def export_qasm(request: Request, body: QasmRequest) -> QasmResponse:
    done = await _container(request).export_qasm(circuit_json=body.circuit_json, values=body.values)
    return QasmResponse.from_domain(done)


@papers_router.get(
    "/{doc_id}/circuits",
    operation_id="listDocumentCircuits",
    summary="The circuit blocks of a paper",
    description="Every fenced `latex` block of the paper, numbered from 1 over its whole text as "
    "`docingest circuits export` numbers them, with what docingest read in it and each circuit "
    "as a `circuit/1` document.",
    responses=_PAPER_PROBLEMS,
)
async def list_document_circuits(doc_id: DocId, request: Request) -> PaperCircuitsResponse:
    return PaperCircuitsResponse.from_domain(await _container(request).list_paper_circuits(doc_id))


@papers_router.post(
    "/{doc_id}/qasm",
    operation_id="exportDocumentQasm",
    summary="Export every circuit of a paper as OpenQASM 3",
    description="The files `docingest circuits export --doc` would write for the paper, byte for "
    "byte and under the same names, returned instead of written: nothing changes on the server. "
    "All the circuits of the paper share one operation cap. " + QASM_NOTE,
    responses=_PAPER_PROBLEMS,
)
async def export_document_qasm(
    doc_id: DocId, request: Request, body: PaperQasmRequest
) -> PaperQasmResponse:
    done = await _container(request).export_paper_qasm(doc_id, values=body.values)
    return PaperQasmResponse.from_domain(done)
