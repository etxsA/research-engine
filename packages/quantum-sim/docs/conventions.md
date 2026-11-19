# Conventions

quantum-sim follows Nielsen and Chuang, *Quantum Computation and Quantum Information*. These are the choices that decide whether a result is right or silently wrong: the wire order, the sign and the factor 1/2 of a rotation, and the difference between a rotation and a phase gate once they are controlled. The same table is in `quantum_sim.domain` (`CONVENTIONS`, `GATES`) and is printed by `quantum-sim conventions`; a test checks that every gate there has a row below.

## Wires and states

| Item | Convention |
|---|---|
| Wire order | Wire 1 is the top wire of a drawing and the **most significant** bit. A basis state is written `\|w1 w2 ... wn>`, and amplitude `k` of a state vector belongs to the basis label that is `k` in binary. Example: X on wire 1 of 2 maps `\|00>` (amplitude 0) to `\|10>` (amplitude 2). Same as Nielsen and Chuang, Cirq and docingest's gate lists; the **opposite of Qiskit**, whose qubit 0 is the least significant bit. |
| Wire numbers | 1-based in every user-facing structure (`Op.wires`, `controls`, `probabilities(wires=...)`); 0-based only inside the engine. |
| Matrices | Rows and columns are indexed like state amplitudes, so `unitary(c) @ v == statevector(c, v)`. A two-wire gate's matrix uses the order of `Op.wires`: the first target wire is the more significant bit. |
| Input state | `\|0...0>` unless the caller gives a basis label such as `"0110"` (wire 1 first) or `2^n` amplitudes whose norm is 1 within 1e-9. A ket in a wire legend or a text item is not read as an input state (see Wire legends below). |
| Step order | Operations apply left to right, in the order of `Circuit.ops`. |

## Gates

Names are exact and lowercase; anything else is refused (`UnknownGateError`), never guessed. Every matrix is exact, global phase included, because a controlled gate exposes the phase of its target matrix.

| Name | Targets | Parameters | Matrix |
|---|---|---|---|
| `id` | 1 | 0 | I = [[1, 0], [0, 1]] |
| `h` | 1 | 0 | H = (1/√2) [[1, 1], [1, −1]] |
| `x` | 1 | 0 | X = [[0, 1], [1, 0]] |
| `y` | 1 | 0 | Y = [[0, −i], [i, 0]] |
| `z` | 1 | 0 | Z = [[1, 0], [0, −1]] |
| `s` | 1 | 0 | S = P(π/2) = diag(1, i) |
| `sdg` | 1 | 0 | S† = diag(1, −i) |
| `t` | 1 | 0 | T = P(π/4) = diag(1, e^{iπ/4}) |
| `tdg` | 1 | 0 | T† = diag(1, e^{−iπ/4}) |
| `sx` | 1 | 0 | √X = (1/2) [[1+i, 1−i], [1−i, 1+i]] |
| `sxdg` | 1 | 0 | √X† = (1/2) [[1−i, 1+i], [1+i, 1−i]] |
| `rx` | 1 | θ | R_x(θ) = exp(−iθX/2) = [[cos θ/2, −i sin θ/2], [−i sin θ/2, cos θ/2]] |
| `ry` | 1 | θ | R_y(θ) = exp(−iθY/2) = [[cos θ/2, −sin θ/2], [sin θ/2, cos θ/2]] |
| `rz` | 1 | θ | R_z(θ) = exp(−iθZ/2) = diag(e^{−iθ/2}, e^{iθ/2}) |
| `p` | 1 | θ | P(θ) = diag(1, e^{iθ}) |
| `rk` | 1 | k | R_k = diag(1, e^{2πi/2^k}), k a positive integer: the controlled rotation of the quantum Fourier transform. R_1 = Z, R_2 = S, R_3 = T. |
| `rxx` | 2 | θ | R_xx(θ) = exp(−iθ X⊗X / 2) = cos(θ/2) I − i sin(θ/2) X⊗X |
| `ryy` | 2 | θ | R_yy(θ) = exp(−iθ Y⊗Y / 2) = cos(θ/2) I − i sin(θ/2) Y⊗Y |
| `rzz` | 2 | θ | R_zz(θ) = exp(−iθ Z⊗Z / 2) = diag(e^{−iθ/2}, e^{iθ/2}, e^{iθ/2}, e^{−iθ/2}) |
| `swap` | 2 | 0 | `\|ab>` → `\|ba>` |
| `iswap` | 2 | 0 | [[1, 0, 0, 0], [0, 0, i, 0], [0, i, 0, 0], [0, 0, 0, 1]] |

Useful consequences, all tested: S² = Z, T² = S, √X² = X, HXH = Z, HZH = X, HYH = −Y, and R_z(θ) = e^{−iθ/2} P(θ).

## Controls

| Item | Convention |
|---|---|
| Closed control | `Op.controls`: the gate acts when the wire is `\|1>`. |
| Open control | `Op.open_controls`: the gate acts when the wire is `\|0>`. |
| Any number, any gate | Every gate in the table takes any number of closed and open controls. Controls are never part of a gate: CNOT is `Op("x", (t,), controls=(c,))`, Toffoli is `Op("x", (t,), controls=(c1, c2))`, Fredkin is `Op("swap", (a, b), controls=(c,))`, and two more controls on a CNOT make a 3-control X. |
| Controlled R_z is not controlled P | R_z(θ) and P(θ) differ by the global phase e^{−iθ/2} alone, but controlled that phase becomes relative: C-R_z(θ) = diag(1, 1, e^{−iθ/2}, e^{iθ/2}) and C-P(θ) = diag(1, 1, 1, e^{iθ}) are different gates. This is why the label resolver (see Gate labels below) refuses labels such as `R(\theta)`, `R_\phi`, `U_1` or `Ph(...)` instead of mapping them. |

## Gate labels

The circuit format reads gates by the label an author wrote in LaTeX. The resolver (`quantum_sim.resolve_label`) has a closed table of names and a small grammar around them. It does not fold case beyond the spellings below, does not match by similarity, and refuses everything else with a reason, because a wrong gate is worse than none.

A label is a name, optionally followed by an angle in parentheses, with at most one dagger before or after the angle. Font commands around plain letters (`\mathrm{R}`, `\text{H}`, `\operatorname{SWAP}`), `\left` and `\right` around the parentheses, spacing commands and enclosing `$` or braces are removed first. A dagger is `^\dagger`, `^{\dagger}`, `^\dag`, `^{\dag}`, `^†`, `†` or `^{-1}`; a superscript of more than one character must be in matching braces, as LaTeX sets it (`T^-1` is a T with a lone minus, `X^1/2` is X¹ over 2, `X^{1/2` is unbalanced: all refused), a superscript on `X^{1/2}` is a double superscript, which LaTeX does not set (`X^{1/2}^\dagger` is refused; `{X^{1/2}}^\dagger` is read); a font command right after `_` or `^` is the whole subscript or superscript (`R_\mathrm{zz}` is `R_{zz}`); and a rotation about two axes needs braces too (`R_xx` is R_x followed by x; write `R_{xx}`); it turns S into S†, T into T†, √X into √X†, negates the angle of a rotation or phase gate, and leaves H, X, Y, Z, I, SWAP and CZ as they are. The inverse of iSWAP and of R_k is not in the gate table, so those are refused. Every spelling below is tested, and a test checks that this table lists exactly the spellings the resolver accepts (`θ` stands for any angle).

| Gate | Table name | Accepted spellings |
|---|---|---|
| I (identity) | `id` | `I`, `Id`, `\mathbb{I}`, `\mathbb{1}`, `\mathds{1}` |
| H | `h` | `H`, `\mathrm{H}`, `\text{H}` |
| X | `x` | `X`, `\mathrm{X}` |
| Y | `y` | `Y`, `\mathrm{Y}` |
| Z | `z` | `Z`, `\mathrm{Z}` |
| S | `s` | `S`, `\mathrm{S}` |
| S† | `sdg` | `S^\dagger`, `S^{\dagger}`, `S^\dag`, `S^{\dag}`, `S^†`, `S†`, `S^{-1}`, `Sdg`, `\mathrm{S}^\dagger` |
| T | `t` | `T`, `\mathrm{T}` |
| T† | `tdg` | `T^\dagger`, `T^{\dagger}`, `T^\dag`, `T^{\dag}`, `T^†`, `T†`, `T^{-1}`, `Tdg`, `\mathrm{T}^\dagger` |
| √X | `sx` | `\sqrt{X}`, `\sqrt X`, `\sqrt{\mathrm{X}}`, `\sqrt{\mathrm{NOT}}`, `SX`, `X^{1/2}` |
| √X† | `sxdg` | `\sqrt{X}^\dagger`, `\sqrt{X}^{\dagger}`, `\sqrt{X}^{-1}`, `X^{-1/2}` |
| R_x(θ) | `rx` | `R_x(\theta)`, `R_{x}(\theta)`, `R_X(\theta)`, `RX(\theta)`, `Rx(\theta)`, `\mathrm{R}_x(\theta)` |
| R_y(θ) | `ry` | `R_y(\theta)`, `R_{y}(\theta)`, `R_Y(\theta)`, `RY(\theta)`, `Ry(\theta)`, `\mathrm{R}_y(\theta)` |
| R_z(θ) | `rz` | `R_z(\theta)`, `R_{z}(\theta)`, `R_Z(\theta)`, `RZ(\theta)`, `Rz(\theta)`, `\mathrm{R}_z(\theta)` |
| P(θ) | `p` | `P(\theta)`, `\mathrm{P}(\theta)` |
| R_k (the QFT's) | `rk` | `R_2`, `R_{2}`, `R_{k=12}`, `\mathrm{R}_3` |
| R_xx(θ) | `rxx` | `R_{xx}(\theta)`, `R_{XX}(\theta)`, `RXX(\theta)`, `Rxx(\theta)`, `R_{\mathrm{xx}}(\theta)` |
| R_yy(θ) | `ryy` | `R_{yy}(\theta)`, `R_{YY}(\theta)`, `RYY(\theta)`, `Ryy(\theta)` |
| R_zz(θ) | `rzz` | `R_{zz}(\theta)`, `R_{ZZ}(\theta)`, `RZZ(\theta)`, `Rzz(\theta)` |
| SWAP | `swap` | `SWAP`, `\mathrm{SWAP}`, `\text{SWAP}` |
| iSWAP | `iswap` | `iSWAP`, `\mathrm{iSWAP}` |
| CZ | `z` with one control (the first drawn wire) | `CZ`, `\mathrm{CZ}` |

`R_k` takes a number and no angle: one digit from 1 to 9 (`R_2` is S, `R_3` is T, `R_1` is Z), or `R_{k=12}` for a k of more than one digit, which is also how the writer spells it. With a letter (`R_k`) it is refused, and so is an index of several digits or with a leading zero (`R_12`, `R_{12}`, `R_{01}`): `R_12` is R_1 followed by a 2 in LaTeX, and a pair of digits usually names a pair of wires or levels. The axis of a rotation is one letter, `x`, `y` or `z`, in either case, or the same letter twice for the two-wire rotations. A one-qubit label over several target wires is that gate on each wire, with the item's controls on each, when the document is hand-written or from yquant (`h a, b`); in a document from quantikz, qcircuit or tikzcd it is refused, because `\gate[2]{H}` is one box over two wires and does not say it is H on each. A two-wire label needs exactly two wires; CZ, SWAP, iSWAP and the two-wire rotations are symmetric in them.

**Refused, never mapped.** The reason starts `unknown gate label '...'`; for the families below it goes on `(ambiguous: ...)`.

| Label | Why it is refused |
|---|---|
| `R(\theta)`, `R`, `R_\phi`, `R_{\theta}`, `R_k` | the axis is not given: a rotation about an axis and a phase gate differ by a global phase uncontrolled and by a relative phase controlled |
| `U`, `U_1`, `U_2`, `U_3`, `U(...)`, `\hat{U}`, `\hat{U}_t(...)`, `\tilde{U}`, `\mathcal{U}`, `V`, `V_0`, `W` | each paper defines them (`U_1` is a phase gate in one source and the first of a family in another) |
| `Ph`, `Ph(\phi)`, `Phase(...)` | a phase gate or a global phase |
| `CNOT`, `CX`, `CCX`, `CCNOT`, `Toffoli`, `Fredkin`, `CSWAP` as a box label | the label does not say which wire is the control; the drawn control dots do (`x` and `swap` items of the format) |
| anything else (`h`, `Hadamard`, `H^{\otimes n}`, `T^2`, `\sqrt{Z}`, `X(\theta)`, `CH`, `CR_z(\theta)`) | not in the table; no fuzzy matching |

## Wire legends and text on a wire

Gate labels are read from a closed table. Wire legends and text on a wire are read the same way: by what they may hold, not by what they must not. A postselection (a bra or a projector at the end of a wire, `\rstick{\bra{1}}`) keeps only some of the runs and version 1 does not simulate it. It can be drawn in more ways than any list names (`\bra{1}`, `\langle 1|`, `\ketbra{1}{1}`, `\left<1\right\vert`, the paper's own macros such as `\kb{1}{1}` or `\Proj{1}`, brackets and bars in Unicode, words such as "post-selected"), so no spelling is looked for. A legend or a text item is read only if it fully matches the grammar below; anything else refuses the whole circuit.

| Where | Read when | Otherwise |
|---|---|---|
| Input part of a legend (`N = label`, before `output`) | empty, a plain name, or a ket of one basis state | refused with `input legend not read: ...` |
| Output part of a legend (`output label`) | empty, or a plain name | refused with `output legend not read: ...` |
| Text on a wire | empty, or a ket of one basis state, before any gate on its wire, and not in the step of one | refused with `text ... on a wire is not a gate and not a ket`, or `... is a ket after a gate on its wire or in the column of one`, or `... is a ket on no wire` |
| Title of a slice (`barrier` label) | empty, or a plain name as in an output | refused with `the slice title ... is not read` |

- A **legend** is a line as docingest writes it: `N`, `N = label`, `N = label, output label` or `N = output label`; a label for several wires ends in ` (one label for wires 1 and 2)`. White space is one space. Only ASCII is read (a Unicode space and an Arabic-Indic digit are not). A legend that is only a number is a wire with no label. A legend that starts with a number other than its place (`["2 = q", "1 = r"]`) is refused: which legend is on which wire is unknown.
- A **ket of one basis state** is `\ket{0}`, `\ket0`, `\ket 0`, `|0\rangle`, `\left|0\right\rangle`, `\lvert 0\rangle` or `\vert 0\rangle`, and the same with 1, alone or between `$` or braces. Not read: `\ket{+}` and `\ket{-}` (the engine takes only a basis label or amplitudes as an input), `\ket{\psi}`, `|q_0\rangle`, a ket of two bits, `\Ket{1}`, a ket with an operator or a mark (`H\ket{0}`, `\ket{0}_x`, `\ket{0}^T`), a ket that stands for several wires. An output is never a ket, `\ket{1}` too.
- A **plain name** is one letter (alone or in `\mathrm{}` or `\text{}`), up to two digits, a subscript (one character, or up to eight letters, digits, `+`, `,` and `-` in braces; in an output or a slice title never two letters in a row, so `q_{postsel}` and `x_{ps}` are not names) and primes: `A`, `q_0`, `x'`, `S_{2m-1}`, `q_{2n,0}`, `\mathrm{A}_1`, `{$v_0$}`. Never a word (`data`, `anc`, `excited`, `postselect 1`) and never digits alone (`output 1` may be a postselection on 1). A name that looks like a projector's (`P_1`, `M_0`) is read as the name of a wire: the grammar cannot tell the two apart.
- A ket |1> is not read as an input state: the input is the one the caller gives (|0...0> by default), and a note names the legend or the place of the text. A ket |0> is silent. Every basis ket at an input is recorded in `Reading.input_kets` as `(wire, "0" | "1")`, so a caller such as `to_qasm` can refuse a `|1>` it cannot state as an operation. A ket in a text item after a gate on its wire or in the same step as one (a step has no order), |0> too, is refused (it may be a reset or a postselection).
- The document `label` is not read: a non-empty one gives a note (`the document label ... is not read`); one that is not text is refused, and so is a `kind` that is not text. A key written twice in the JSON is refused by the command, and so is a symbol given two values, also under two spellings (`theta` and `\theta`, `--set` too): the last value would win silently.
- A legend that shows a bundle, a register, a row of dots or a classical wire (`2 = a, bundle m`, `3 = dots (more wires, not drawn)`, `\qwbundle`, `\cw`, `cbit`) is refused for that, before the grammar.

## Angles

An angle is read by a small parser (`quantum_sim.parse_angle`) that never evaluates code. It reads numbers (`1`, `0.5`, `.5`), `\pi`, `\frac{a}{b}` (also `\dfrac`, `\tfrac`), `\sqrt{a}`, `+`, `-`, `*`, `/`, `\cdot`, `\times`, `\div`, parentheses and braces, unary signs, integer powers (`2^{-3}`), and implicit multiplication (`2\pi`, `\pi/4`, `2\gamma`). A symbol (`\theta`, `\alpha_1`, `\alpha_{12}`, `t`) becomes a free parameter: the circuit is then symbolic and is simulated at values given with it, or reported as symbolic.

Refused, with the reason: text left over after the expression; a division followed by a factor with no operator (`1/2\pi` could be one half of pi or one over two pi); two numbers in a row (`2 3`, and also `2{5}`, `{2}5` and `{2}{5}`, which LaTeX shows as 25: braces group but do not separate); a sign in braces right after a factor (`2{-5}` is shown as 2-5); a number right before a fraction of two numbers (`2\frac{1}{2}` is two and a half in LaTeX, a mixed number, also when braces, parentheses or signs wrap the numbers; `2\frac{\pi}{3}` is still a product); a number starting with a point inside braces right after a factor (`\pi{.5}`, `2{.5}`); exponent notation (`1e3`) and the letters `e`, `E`, `i` and `j` as symbols (they may be constants); `\frac12` without braces; a digit right after an unbraced subscript (`\theta_12` is `\theta_1` followed by 2 in LaTeX; write `\theta_{12}`); a number starting with a point right after a factor (`\pi.5` is shown as pi.5, not as pi times 0.5; write `\pi * 0.5`); two or more ASCII letters in a row (`cos\theta`, `xy`: a function or a product, write the symbols with a space); a symbol, alone or with a power, right before a parenthesis (`\phi(x)`, `f(x)`, `\theta(t)`, `\phi_{1}(x)`: a function or a product; a number, `\pi` or a closing parenthesis before a parenthesis stay products: `2(\pi - x)`, `(\pi - x)(\pi - y)`); a number right after a symbol (`\theta1`, `\theta 2`, `x1`, `\theta_1 2`: a name or a product); an exponent that is not an integer constant of at most 64, or `2^10` without braces (LaTeX reads it as `2^{1}0`); a superscript that opens with a parenthesis (`\theta^{(2)}`, `x^{(0)}`: in a paper it numbers the layer of a parameter, it is not a power, and `\theta^{(1)}` and `\theta^{(2)}` would read as one symbol; write `\theta_{2}` for another parameter or `\theta^2` for a power); division by zero; a result that is not finite; text over 500 characters or nested over 40 levels.

## Equality

Two states or two unitaries are **equal up to global phase** when max |a − e^{iφ} b| < 1e-9, with φ taken from the largest entry of b (φ in (−π, π]). Exact equality (max |a − b| < 1e-9) is reported too. Two circuits are compared through their unitaries up to 10 wires; from 11 to 20 wires through the images of 8 random states (drawn with `numpy.random.default_rng(seed)`, default seed 0) with one common phase, read at the largest entry of the first image, without building a unitary.

## Measurement

Version 1 measures in the computational basis at the **end of a wire** only. After a wire's measurement no operation may use that wire (as target, control or open control), and classically controlled operations (`Op.classical_controls`) are refused. Results are outcome probabilities; `statevector` returns the state just before the final measurements, and `run` reports it that way; a check (between circuits, against a matrix or against a state) refuses a circuit with measurements, because there is no unitary and no single state to compare. Deferred measurement (exact for outcome statistics) is a possible version 2.

## Limits

| Quantity | Limit | Memory at the limit (complex128) |
|---|---|---|
| Statevector, probabilities, equality by random states | 20 wires | 16 MiB per state |
| Unitary | 10 wires | 16 MiB |
| Operations per circuit | 10,000 | |

A request above a limit is refused from the circuit's description, before any array is created (`LimitExceededError`).

## Comparing with other libraries

| Library | Order | To compare with quantum-sim |
|---|---|---|
| Qiskit | qubit 0 is the least significant bit | place wire w on qubit w − 1 and reverse the operator's qubit order (`Operator(qc).reverse_qargs()`), as `tools/make_reference.py` does |
| Cirq | the first qubit is the most significant bit | the same order |
