# quantum-sim

A small simulator of quantum circuits in NumPy: the state a circuit prepares, its unitary, outcome probabilities, and whether two circuits (or a circuit and a target) are equal up to global phase. It is meant for checking circuits, the ones a model or an agent writes and the ones docingest reads from a paper's LaTeX, and it states its conventions with every result. numpy is its only dependency; it does not import docingest, and docingest does not import it.

**Status: usable from Python and from the command line.** The engine (statevector, unitary, outcome probabilities, equality up to global phase), the gate-label resolver and angle parser, the `circuit/1` JSON format and the `run` and `check` commands are implemented and tested, including the path from LaTeX circuit source through docingest's gate lists to a unitary.

The decision and its alternatives are in [ADR 0005](../docingest/docs/adr/0005-own-numpy-circuit-simulator.md).

## Contents

- [Use it](#use-it)
- [The circuit format](#the-circuit-format)
- [The commands](#the-commands)
- [Labels and angles](#labels-and-angles)
- [From LaTeX source](#from-latex-source)
- [Conventions](#conventions)
- [Limits](#limits)
- [How the engine works](#how-the-engine-works)
- [Code map](#code-map)
- [Tests and checks](#tests-and-checks)
- [The reference fixture](#the-reference-fixture)

## Use it

```python
from quantum_sim import Circuit, Op, equal, probabilities, statevector, unitary

bell = Circuit(2, [Op("h", (1,)), Op("x", (2,), controls=(1,))])
statevector(bell)  # [0.707, 0, 0, 0.707]: |00> and |11>
probabilities(bell)  # [0.5, 0, 0, 0.5]

# CNOT = (I ⊗ H) CZ (I ⊗ H), compared up to global phase
cz_form = Circuit(2, [Op("h", (2,)), Op("z", (2,), controls=(1,)), Op("h", (2,))])
equal(cz_form, Circuit(2, [Op("x", (2,), controls=(1,))])).equal  # True
```

| Function | Returns |
|---|---|
| `statevector(circuit, input_state=None)` | `2^n` amplitudes after the circuit; the input is `\|0...0>`, a basis label such as `"010"` or a list of amplitudes |
| `unitary(circuit)` | the `2^n x 2^n` matrix (up to 10 wires; no measurements) |
| `probabilities(circuit, input_state=None, wires=None)` | outcome probabilities of the measured wires, or of the wires given, first wire most significant |
| `equal(a, b, atol=1e-9, seed=0)` | an `Equality` (`equal`, `exact`, `phase`, `max_deviation`, `method`) for two circuits, a circuit and a matrix, or two arrays |
| `validate(circuit)` | nothing, or the typed error that says why the circuit cannot be simulated |
| `from_gate_list(document)` | a `Reading` (one `ParametricCircuit` per part of a `circuit/1` document) or a `RefusedError` that lists every reason |
| `to_gate_list(circuit)` | the circuit as a `circuit/1` document; reading it back gives the same circuit |
| `run(request, ...)`, `check(request, ...)` | a JSON-ready result dict (`quantum-sim-result/1`); bad input is a result with `status` `"refused"`, never an exception |
| `resolve_label(label)`, `parse_angle(text)` | a gate of the table with its angles, or a `LabelError` / `AngleError` with the reason |

An `Op` is a gate name from the table, its target wires (1-based), closed `controls`, `open_controls`, `params`, and `classical_controls` (refused in version 1). The measurement op is `Op("measure", (w,))`, at the end of its wire only. Errors derive from `QuantumSimError`: `InvalidCircuitError` (and `UnknownGateError`), `UnsupportedOperationError`, `LimitExceededError`, `InvalidStateError`.

## The circuit format

`circuit/1` is the shape that docingest's `CircuitSteps.to_dict()` produces (the gate list of a paper's circuit as data), and the shape an agent can write by hand. Only `format`, `wires` and `parts` are needed; each item needs a `kind`, and a `label`, `targets`, `controls` and `open_controls` as the kind asks. Wires are 1-based, top to bottom.

```json
{"format": "circuit/1", "wires": 2,
 "parts": [[[{"kind": "gate", "label": "H", "targets": [1]}],
            [{"kind": "x", "label": "X", "targets": [2], "controls": [1]}]]]}
```

`parts` holds one list of steps per side of a drawn equality (the second part starts with a step that holds a `sep` item, the sign). A step is a list of items. A key written twice in the JSON (`"controls"` twice in an item, `"wire_labels"` twice in a document) is refused by `quantum-sim run` and `check`, with the name of the key: the last value would win and the document does not say which is meant. The Python API takes dicts and cannot see a repeated key, so a caller that builds a document from JSON text must refuse it too (`json.loads` with an `object_pairs_hook`). The keys of a document are `format`, `kind`, `status`, `label`, `wires`, `wire_labels` and `parts` (the document `label` is docingest's `\label{...}`: free text that is not read, so nothing written there changes a result; a non-empty one gives a note, `the document label ... is not read; if it names a projection, the result is not postselected`); the keys of an item are `kind`, `label`, `targets`, `controls`, `open_controls`, `classical` and `source`. A key that is none of these is refused with its name and, for an item, its place, not ignored: a misspelled `controls` (`control`, `ctrls`) would otherwise leave a CNOT as an X. `input`, `values` and `unitary` belong in the request around the circuit (`{"circuit": ..., "input": "1"}`), not inside it. The kinds that are read:

| `kind` | Meaning |
|---|---|
| `gate` | the gate the `label` names (see below) on `targets`, with `controls` (on \|1>) and `open_controls` (on \|0>); a one-qubit label over several targets is that gate on each, except in a quantikz, qcircuit or tikzcd document, where one box over several wires is refused |
| `x` | X on each target, with the controls (CNOT, Toffoli, ...); the label is `X` or empty |
| `z_dots` | control dots only (label `Z` or empty, no target): a Z carried by the last closed dot, the other dots controlling it |
| `swap` | SWAP of two wires (label empty or `SWAP`), with controls for a Fredkin |
| `measure` | computational-basis measurement at the end of a wire |
| `barrier`, `text` | neutral; a `barrier` has no effect, but its `label` (a slice title in docingest) is free text, so it is read like an output: empty or a plain name (`t_1`), and a word or anything else (`postselect on 1`, `$q_1 = 1$`) is refused; `text` has no controls, and only when it is empty or a ket of a computational-basis state (`\ket{0}`, `\ket{1}`, `\|0\rangle`), and anything else (a bra, a projector, an expectation value, a ket of another state, words) is refused: text is read by what it may hold, not by what it must not |

The whole circuit is refused, with the place of every cause (`part 1, step 3, item 1: ...`), if it holds anything else: an `unparsed*` item, a `status` other than `parsed`, a row of `dots`, a `dots_gate`, a `control` with nothing joined, a `discard`, a `sign`, a `phase`, a `sep` inside a part, an unknown `kind`, a classically controlled item, an item with drawn text beside it (`source`), a measurement in another basis or with controls, text on a wire that is not a basis ket or a wire legend that is not read (below), a legend or a text item of more than 700 characters (the limit of a gate label), a gate label that is unknown or ambiguous, a swap or control dots with a label of another gate, control dots with a target, a one-qubit label on one box over several wires in a quantikz, qcircuit or tikzcd drawing (`\gate[2]{H}` does not say it is H on each wire), a document `label` or `kind` that is not text, a wire named twice in one item, a wire used by two items of one step (a step is a column of the drawing and has no order), an operation on a wire after its measurement, a wire legend numbered for another wire (`["2 = q", "1 = r"]`: which legend is on which wire is unknown; the number is compared as text, so `"01 = q"` on wire 1 is refused too), or a wire legend that shows a bundle (`2 = a, bundle m`), a register, a row of dots (`3 = dots (more wires, not drawn)`) or a classical wire. Nothing is skipped to make a circuit simulable. The notation (`kind`) of a document is compared without case, spaces, hyphens or underscores (`TikZ-cd` is `tikzcd`).

**Wire legends and text are read by what they may hold.** A postselection (`\rstick{\bra{1}}`, a projector at the end of a wire) keeps only some of the runs, and version 1 does not simulate it. It can be drawn in more ways than any list names: `\bra{1}`, `\langle 1|`, `\ketbra{1}{1}`, `\left<1\right\vert`, a macro of the paper's own (`\kb{1}{1}`, `\Proj{1}`, `\braket{1}{1}`), brackets and bars in Unicode, or words (`\text{post-selected on } 1`). Reviews kept finding one more spelling, so no spelling is looked for: a legend or a text item is read only if it fully matches a small closed grammar, and a circuit with anything else is refused.

- A legend is a line as docingest writes it: `N`, `N = label`, `N = label, output label` or `N = output label`, where a label for several wires ends in ` (one label for wires 1 and 2)`. White space is one space.
- The **input** part (before `output`) is read if it is empty, a plain name, or a ket of one computational-basis state: `\ket{0}`, `\ket0`, `\ket 0`, `|0\rangle`, `\left|0\right\rangle`, `\lvert 0\rangle`, `\vert 0\rangle`, and the same with 1, alone or between `$` or braces. A ket of two bits, `\ket{+}`, `\ket{\psi}`, `|q_0\rangle`, an operator or a mark on a ket (`H\ket{0}`, `\ket{0}_x`) and a ket over several wires are not read.
- The **output** part is read only if it is empty or a plain name whose subscript holds no two letters in a row (`v_1`, `S_{2m-1}` and `q_{2n,0}` are read; `q_{postsel}`, `x_{ps}` and `P_{proj}` are not). An output ket (`\ket{1}` too), bra, bar, projector, macro, word or number (`output 1` may be a postselection on 1) refuses the whole circuit with `output legend not read: ...`. An input that is not read is refused with `input legend not read: ...`.
- A **plain name** is one letter (alone or in `\mathrm{}` or `\text{}`), up to two digits, a subscript (`q_0`, `S_{2m-1}`, `q_{2n,0}`: one character, or up to eight letters, digits, `+`, `,` and `-` in braces) and primes: `A`, `q_0`, `x'`, `\mathrm{A}_1`, `{$v_0$}`. Never a word (`data`, `anc`, `excited`): a word can say that the run is postselected or what the input is, and no list of words is complete. A name that looks like the name of a projector or an outcome (`P_1`, `M_0`, `o`) is still a name: the grammar cannot tell it from a wire called `P_1`. In an output the braced subscript may not hold two letters in a row, so a short word there is not a name; a separator passes the rule (`x_{p-s}`, `x_{p,s}` and `q_{1+}` are read), the same residual as `P_1`.
- **Text on a wire** is read if it is empty or a ket of a computational-basis state in the spellings above. A bra, a projector, an expectation value (`\langle Z\rangle`, `\expval{Z}`, `\mel{1}{Z}{1}`) or any other text is refused; a gate label is read from the closed table of names (see below), and an unknown one is refused, `\Proj{1}` and `P_1` too.
- A ket |1> in a legend or in a text item is not read as an input state: the result's `input` says which input was used (|0...0> unless given) and a note names the legend or the place of the text. A ket in a text item after a gate on its wire (`\push{\ket{1}}`, a bare `\ket{1}` cell, a second `\lstick`, or the first `\lstick` of a row when it is drawn after a gate or a wire on that row, under a multi-wire box (`\gate[2]{...}`) from the row above, or in the cell of a gate: docingest keeps it as text there, not as the legend), or in the same step as a gate on its wire (a step has no order, so the ket listed before the gate is as ambiguous as the one listed after it), `|0>` too, is refused: it may be a reset or a postselection, which are not simulated. A ket on no wire (`targets: []`) is refused as well. A ket |0> is silent.
- Only ASCII is read: any other character, a Unicode space or an Arabic-Indic digit too, is outside the grammar.
- A legend that is only a number (`"1"`, `"12"`) is the docingest form for a wire with no label: it is read as empty, with no note, even in a hand-written document where it might mean an input or an outcome. Write the label after the number (`1 = q_0`).

One gap is documented, not closed: docingest does not mark a `yquant` `cbit` register as classical, so its legend is plain text and the reader takes the wire for a qubit. That changes the wire count and the size of the unitary of a circuit that declares its classical registers. Look at the wire count against the drawing before trusting a result; marking the wires is a change for docingest. A Qcircuit or quantikz `\cw` is not part of the gap: after a measurement it is the wire the measurement leaves and adds no item, and a `\cw` with no measurement before it in its row, a gate or control drawn on a classical wire (`\ctrl{1} \cw` is no longer a CNOT) and a wire that turns classical or quantum again are unparsed items, so this package refuses the circuit, as it does for a quantikz `\setwiretype{c}`.

What docingest used to drop is now unparsed, so this package refuses the circuit: a second `\rstick` on one row (it replaced the first, `\rstick{\bra{1}} & \rstick{a}` reached this package as `output a`); a `\gateoutput`, `\gateinput`, `\gategroup`, `\cds` or `\inputgroup` label with any text, one letter included; an option an operation does not read (`\ctrl[wire style={"n"}]{1}`, `\gate[2, nwires=1]{U}`, `\gate[style={draw=white,text=white}]{X}`: only a single `fill` of a base colour from 10 percent up is read); a `\setwiretype` or `\wireoverride` other than `q`; a `yquant` value on a gate that takes none (`h {\cdots} a`); a circuit environment option such as `wire types={q,c}`; a `\linethrough` and a vertical wire with nothing joined to it; and any text of the block outside the circuit environments that is not a math delimiter or a figure, centering or label wrapper (`\left(` ... `\right)^{\otimes n}`, `^{\times 3}`, a `\def`, a word between two circuits), which makes every circuit of the block `partial`. docingest reads such a circuit as `partial` (or `unparsed`).

A circuit with symbols (`R_y(\theta)`) is a `ParametricCircuit`: `bind` gives the numbers, or `run` and `check` take `values` (`--set theta=\pi/2`; a name given twice is refused, also under two spellings: `theta`, `\theta` and `{theta}` are one name) and report `status: "symbolic"` with the names in `unbound` when a value is missing.

## The commands

```bash
uv run quantum-sim conventions                   # the conventions, the gate table and the limits
uv run quantum-sim run bell.json                 # state and probabilities of a circuit/1 file
uv run quantum-sim run bell.json --input 10 --unitary   # --unitary adds the matrix; a request file with "unitary": true lists it without the flag
uv run quantum-sim run ry.json --set 'theta=\pi/2'
uv run quantum-sim check a.json b.json           # are two circuits equal up to global phase?
uv run quantum-sim check request.json            # {"a": ..., "b": ...}, {"circuit": ..., "target": ...}
uv run quantum-sim check identity.json           # a circuit/1 with a drawn equality: left against right
cat bell.json | uv run quantum-sim run           # standard input
```

JSON goes in (a file, or standard input for no file or `-`) and one JSON document comes out on standard output, in the format `quantum-sim-result/1`: `status` (`ok`, `symbolic` or `refused`), `conventions` (printed in every result), `input`, `parameters`, `values`, `unbound`, `circuits` (for each part: `state`, `probabilities` and, with `--unitary`, `unitary`; complex numbers as `[re, im]` rounded to 12 decimals), `equal_to_target` (true, false or null), `comparisons` (`equal`, `exact`, `phase`, `max_deviation`, `method`), `notes` and `refused` (the reasons). States are listed up to 12 wires and unitaries up to 8; above that the probabilities give the 64 likeliest outcomes.

| Exit status | Meaning |
|---|---|
| 0 | the work was done: `ok`, or `symbolic` when a symbol has no value; a check that finds the circuits different is still 0, with `"equal_to_target": false` |
| 1 | the input was refused (not JSON, unreadable, not a circuit/1 document, an unknown or ambiguous label, over a limit, ...): the JSON result on standard output says why |
| 2 | the command line does not parse (usage on standard error) |

A check takes `{"a": circuit, "b": circuit}`, `{"circuit": circuit, "target": {"unitary": [[...]]}}`, `{"circuit": circuit, "target": {"state": [...]}}` (with an optional `"input"`; an `input` given with two circuits or a drawn equality is not used and a note says so), or one document with a drawn equality. Matrices and states are lists of numbers or `[re, im]` pairs; a matrix must be unitary and a state of norm 1, and a circuit with measurements is refused in every kind of check (it has no unitary and no single state). `run` takes a circuit/1 document or `{"circuit": ..., "input": "010", "values": {...}, "unitary": true}`; `unitary` must be true or false. An unknown key in a request is refused, not ignored.

```python
from quantum_sim import check, run

bell = {"format": "circuit/1", "wires": 2, "parts": [[
    [{"kind": "gate", "label": "H", "targets": [1]}],
    [{"kind": "x", "label": "X", "targets": [2], "controls": [1]}]]]}
run(bell)["circuits"][0]["probabilities"]["values"]  # [0.5, 0.0, 0.0, 0.5]
check({"circuit": bell, "target": {"state": [0.7071067811865476, 0, 0, 0.7071067811865476]}})[
    "equal_to_target"
]  # True
```

## Labels and angles

The label resolver turns a label as an author writes it in LaTeX (`H`, `T^\dagger`, `\sqrt{X}`, `R_y(\pi/2)`, `R_{zz}(\theta)`, `P(\phi)`, `R_2`, `CZ`, `SWAP`, ...) into a gate of the table, or refuses it. The table of names is closed; `docs/conventions.md` lists every accepted spelling and every refused family with its reason. `R_k` is read for one digit from 1 to 9, or written out as `R_{k=12}`; `R_12`, `R_{12}` and `R_{01}` are refused (R_12 is R_1 then a 2 in LaTeX, and a pair of digits usually names wires or levels). The ambiguous labels (`R(\theta)`, `R_\phi`, `U_1`, `U_3`, `Ph(...)`, `\hat{U}...`, `V`) are refused rather than mapped, because controlled R_z and controlled P differ. A box labelled CNOT, Toffoli or Fredkin is refused too: the label does not say which wire is the control, the drawn control dots (the `x` and `swap` kinds) do. The reason always starts `unknown gate label '...'`.

Angles go through a small parser that never evaluates code: numbers, `\pi`, `\frac{a}{b}`, `\sqrt{}`, `+ - * /`, parentheses, unary signs, integer powers, implicit products (`2\pi`, `\pi/4`). A symbol (`\theta`, `\alpha_1`) is a free parameter. Leftover text, `1/2\pi`, `1e3`, `\frac12`, `\theta_12` (LaTeX reads `\theta_1` then 2; write `\theta_{12}`), `\theta^{(2)}` (a superscript in parentheses numbers a layer, it is not a power; write `\theta_{2}`; any parenthesis inside a braced superscript is refused, `\theta^{1(2)}` too), `cos\theta` (a run of letters may be a function or a product), `\phi(x)` and `f(x)` (a symbol, or a symbol with a power, right before a parenthesis may be a function, the usual notation of a data-encoding angle; `2(\pi - x)` and `(\pi - x)(\pi - y)` stay products), `\theta1` and `\theta 2` (a number right after a symbol may be a name; write `\theta_{1}` or `2\theta`), `2{\theta+1}` and `{\theta/2}x` (braces are not drawn: LaTeX shows 2\theta+1 and \theta/2x, so a brace group that holds a sum, a difference or a division is refused; write parentheses), `{2\theta}^2` and `{-x}^2` (shown as 2\theta^2 and -x^2, so a brace group of more than one factor as the base of a power is refused; `{\theta}`, `2{x}^2` and `{\theta}^{2}` stay), division by zero and the like are refused with a reason.

## From LaTeX source

quantum-sim reads what docingest's `gate_list(source).circuits[i].to_dict()` returns, without importing docingest. The chain is checked from LaTeX to a unitary by `tests/test_bridge.py`: quantikz and Qcircuit sources of a Bell pair, a GHZ state, a CZ, a Toffoli, an R_y(π/2), a CNOT with its control below its target, a Fredkin gate, the three-wire QFT (which must equal the DFT matrix), a Bell pair measured on both wires and two drawn identities (H Z H = X, and CNOT = (I ⊗ H) CZ (I ⊗ H)) are compared with matrices written by hand, and a bundle and a row of dots, which docingest writes as words in the wire legend, and postselected wires (`\rstick{\bra{1}}`, written in the legend as `output \bra{1}`; and the same wire with a macro of the paper's own, `\rstick{\Proj{1}}` and `\rstick{\kb{1}{1}}`, and with words, `\rstick{\text{post-selected on } 1}`) must be refused: an output that is not a plain name is not read, whatever it says. The same holds for a slice title with words (`\slice{postselect on 1}`, `\slice{$q_1 = 1$}`) and for a ket drawn as text after a gate (`\push{\ket{1}}`), for an angle that is a function of a symbol (`R_z(\phi(x))`), for an angle with a sum in braces (`R_y(2{\theta+1})`, shown as 2\theta+1) and for an `\lstick{\ket{0}}` drawn after the gates of a row that has no legend, and for a circuit with a quantikz `\wire[l][1]["\iddots"]{a}` mark, which stands for gates the figure leaves out (docingest marks the mark `unparsed` and the circuit `partial`).

The test cannot call docingest: the lane that tests quantum-sim installs this package alone, and a test that imported both would pass on a workspace checkout and fail in CI. So the sources and the documents docingest makes from them are saved in `tests/fixtures/bridge/` (`NAME.tex` and `NAME.json`; [`tools/make_bridge_fixtures.py`](tools/make_bridge_fixtures.py) writes the second from the first, run where docingest is installed), and docingest has a test (`tests/unit/test_quantum_sim_fixtures.py`) that remakes each document from its source and compares. A change in docingest's output fails there until the fixtures are regenerated; a change in how quantum-sim reads them fails the bridge tests here. Add a circuit by adding its `.tex` file and running the script; a source that docingest must read as `partial` is named `partial_*.tex`.

docingest can also write the same circuit as a program for a quantum SDK: `docingest circuits export` projects the stored circuit blocks onto OpenQASM 3, one program per part, with the drawn sign and provenance in comments ([ADR 0006](../docingest/docs/adr/0006-openqasm-3-export.md)). quantum-sim does not read QASM; the two paths share the reading of the drawing, not a format.

The command `quantum-sim conventions` prints the conventions, the gate table and the limits as JSON.

## Conventions

Nielsen and Chuang throughout, written out in [docs/conventions.md](docs/conventions.md):

- wire 1 is the top wire and the **most significant** bit (`|w1 w2 ... wn>`), the opposite of Qiskit;
- wires are numbered from 1 in every user-facing structure;
- R_a(θ) = exp(−iθσ_a/2), R_aa(θ) = exp(−iθ σ_a⊗σ_a / 2), P(θ) = diag(1, e^{iθ}), S = P(π/2), T = P(π/4), √X = (1/2)[[1+i, 1−i], [1−i, 1+i]], the QFT's R_k = diag(1, e^{2πi/2^k});
- any gate takes any number of closed and open controls, and controlled R_z is not controlled P;
- results are compared up to global phase (1e-9), and exact equality is reported too.

## Limits

Statevectors up to 20 wires (16 MiB), unitaries up to 10 wires (16 MiB), equality of two circuits of 11 to 20 wires through 8 random states, at most 10,000 operations. A `circuit/1` document may hold at most 50,000 items, and the commands read at most 32 Mi characters of input (the limit counts decoded text, not bytes). Limits are checked from the description before any array is created.

The limits bound memory, not time: a unitary of 10 wires costs about 27 ms per operation, so a circuit of 10,000 operations takes about 4.5 minutes. It is working, not hung.

A refusal does not cost more as the document grows: the reader stops at the first 25 causes (`MAX_REASONS`) and the last line of the list says `... reading stopped after 25 causes; there may be more`. A document of 32 MiB that is full of causes is refused in well under a second of reading.

## How the engine works

The state is a tensor with one axis per wire (axis 0 is wire 1, the most significant bit). A gate is applied in place with `numpy.tensordot` over its target axes: the closed and open controls select a slice of the tensor (the control axes are fixed to 1 or 0 and disappear from the slice), the gate's matrix contracts the target axes of that slice, and the result is written back. That is O(2^n) per gate on a state; a unitary is the same code run on the identity matrix, with its columns as a trailing batch axis (O(4^n) per gate). No `2^n x 2^n` gate matrix is ever built.

Equality of two circuits of 11 to 20 wires applies both to 8 random states drawn with `numpy.random.default_rng(seed)`, one state at a time, so two states are in memory and not eight. The global phase is read once, at the largest entry of the first image of the second circuit, and the deviation reported is the worst over all 8 states with that one phase.

## Code map

| Module | Contains | Imports |
|---|---|---|
| `domain.py` | `Circuit`, `Op`, `GateSpec`, `GATES`, `CONVENTIONS`, the limits, `Equality`, the errors, `validate` and `check_params` | standard library only |
| `gates.py` | `matrix(name, params)`: the exact matrix of each gate, global phase included | numpy, `domain` |
| `engine.py` | `statevector`, `unitary`, `probabilities`, `equal` | numpy, `domain`, `gates` |
| `angles.py` | `Angle`, `parse_angle`, `format_number`: the safe angle parser | `domain` |
| `labels.py` | `resolve_label`, `label_of`, `SPELLINGS`: LaTeX gate label to a gate of the table, or a refusal | `domain`, `angles` |
| `formats.py` | `from_gate_list`, `to_gate_list`, `ParametricCircuit`, `Reading`: the `circuit/1` reader and writer | `domain`, `angles`, `labels` |
| `api.py` | `run`, `check`, `refusal`: the result format | `domain`, `engine`, `formats`, `angles`, numpy |
| `entrypoints.py` | the `quantum-sim` command | `api`, `domain` |

Three import-linter contracts in `pyproject.toml` keep this layering (`entrypoints`, `api`, `formats`, then `engine` beside `labels`, then `gates` beside `angles`, then `domain`), keep numpy out of `domain`, `angles`, `labels` and `formats`, and forbid importing docingest, docingest-index, research_api or qa_bench. The reverse direction, docingest never importing `quantum_sim`, is a contract in docingest's `pyproject.toml`.

## Tests and checks

```bash
./scripts/check.sh      # ruff, ruff format, import-linter, pyright, pytest with an 85% coverage floor
```

The acceptance suite in `tests/acceptance/` is the contract for the engine. Most of it runs twice through the `sim` fixture: once on a naive builder written inside the tests (`tests/acceptance/naive.py`: gate matrices typed in from the conventions, full Kronecker products and permutation matrices, no `tensordot`), which checks the tests themselves, and once on the engine.

| Module | Covers |
|---|---|
| `test_gates.py` | every matrix against hand values and the conventions literal, unitarity, S² = Z, T² = S, √X² = X, HXH = Z, HZH = X, HYH = −Y, R_z(θ) = e^{−iθ/2} P(θ), rotations as exponentials, R_k; unknown names and bad parameters refused |
| `test_wire_order.py` | X on wire 1 of 2 maps `\|00>` to `\|10>`, basis labels read wire 1 first, CNOT in every direction on all basis states of 3 and 4 wires, SWAP and iSWAP on non-adjacent wires |
| `test_controls.py` | CZ, CS, CH, C-R_y, C-P, C-R_z, C-√X, C-R_k on all four inputs with the control above and below; C-R_z is not C-P; open, mixed and extra controls; CZ symmetric; Fredkin; controlled R_zz |
| `test_textbook.py` | Bell and GHZ states, SWAP from 3 CNOTs, CNOT = (I ⊗ H) CZ (I ⊗ H), the exact Toffoli decomposition of Nielsen and Chuang Fig. 4.9 (6 CNOTs, 7 T), CNOT (I ⊗ R_z(2γ)) CNOT = exp(−iγ Z⊗Z), the 3-wire QFT against the DFT with and without its final swap |
| `test_reference.py` | 240 random circuits against Qiskit's unitaries from the fixture, tolerance 1e-10 |
| `test_naive_vs_engine.py` | engine against the naive builder on random circuits from hypothesis (1 to 6 wires, every gate, random controls and angles): unitaries, states, probabilities, `equal` |
| `test_limits.py` | 21-wire statevectors, 11-wire unitaries and 10,001 operations refused with peak memory under 4 MiB (`tracemalloc`); 20 wires, 10 wires and 10,000 operations accepted |
| `test_measurement.py` | end-of-wire measurement and its probabilities; operations after a measurement and classically controlled ones refused |
| `test_equality.py` | global phase, exactness, the phase's range, tolerance, circuits against matrices, random-state equality for 11 to 20 wires |
| `test_validation.py` | unknown names, wires out of range or repeated, wrong numbers of targets and parameters, bad angles, bad input states |

`tests/acceptance/test_engine_edges.py` pins what the suite above leaves open: argument types and the order of the errors, numpy integers as wires, an `rk` given as `3.0`, an `rk` of any size, an `atol` that is not a positive finite number, ops that are not `Op`, input states that are not vectors, circuits against matrices on either side with the phase kept in order, the exact recipe of random-state equality (8 states, one phase, the seed), and the order of a gate's own wires, which is invisible through the gate table because every two-wire gate in it is symmetric.

Beside the acceptance suite:

| Module | Covers |
|---|---|
| `test_angles.py` | the grammar, every refusal with its reason, adversarial inputs (code, unicode digits, deep nesting, huge exponents and numbers, token soup), that floats written by `format_number` are read back exactly |
| `test_labels.py` | every spelling of every gate, the daggers, rotations and phases, the ambiguous families, unknown labels, malformed labels, that a dagger is the conjugate transpose, fuzzing |
| `test_formats.py` | every kind that is read, every refusal (unparsed kinds, unknown kinds, classical control, wire legends, wire ranges, a wire named twice in an item or shared by two items of a step, swap and control dots with a label or target of another gate, one-qubit boxes over several wires, every spelling of a bra, a bar, a projector, a macro, an expectation value, a word or a ket of another state that reviews found, in an input legend, an output legend and a text item (all refused), the ket spellings that are read, a property test that a legend that is read holds nothing a bra could be spelled with, unknown keys in an item or a document, parts without signs, size limits, measurements followed by gates), the notes for kets, several reasons at once, symbols, round trips of random circuits through JSON, any JSON in and a reading or a refusal out |
| `test_api.py` | the result format of `run` and `check`, symbols and values, targets (unitary, state), limits, drawn equalities, that no input makes either function raise |
| `test_cli.py` | `conventions`, `run` and `check` on files and standard input, options, exit codes 0, 1 and 2, bad JSON, unreadable and oversized input, the command run as a subprocess |
| `test_bridge.py` | LaTeX source to unitary through docingest's saved documents, against matrices written by hand |
| `test_wire_names.py` | the wire-name grammar against `fixtures/wire_names.json`, the sample list that docingest's copy of the grammar is tested on too, so the two cannot drift |
| `test_basis_kets.py` | the grammar of a basis ket (`\ket{0}`, `\ket{1}`) against `fixtures/basis_kets.json`, the sample list that docingest's copy, which decides which text it leaves on a wire, is tested on too |
| `test_domain.py`, `test_packaging.py` | the domain types; numpy as the only dependency; every gate and every label spelling documented in `docs/conventions.md` |

## The reference fixture

`tests/fixtures/reference-circuits.json` holds 240 seeded random circuits (up to 5 wires, up to 30 operations, every gate, closed and open controls) and their unitaries as computed by Qiskit, converted to this package's wire order, with the library, its version and the seed in the header. Qiskit is not a dependency of anything in the workspace: [`tools/make_reference.py`](tools/make_reference.py) is run once in a throwaway environment outside the repository, and its docstring shows how. Regenerate the fixture only when the gate table changes.

The fixture cannot tell the order of a gate's own target wires, because every two-wire gate in the table (`swap`, `iswap`, `rxx`, `ryy`, `rzz`) is symmetric; that order is pinned by a test in `tests/acceptance/test_engine_edges.py` that patches in an asymmetric gate matrix, not by Qiskit.
