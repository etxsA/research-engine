"""A small invented corpus for fake mode: six short papers about quantum computing.

Everything here is made up for the demo and the tests (titles, authors, numbers and prose); none
of it comes from a real paper. The text is shaped like the converted papers the real service
holds: Markdown with LaTeX math, one string per page, and for a LaTeX source one string per
section with a ``quantikz`` code block.

The teleportation paper's "The circuit" section holds two blocks: the teleportation circuit, which
the OpenQASM export refuses (it is read as ``partial``: classically controlled corrections), and
the Bell-pair preparation, which exports, so fake mode shows both answers of a paper export.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class FakePage:
    markdown: str
    title: str | None = None  # a section title (LaTeX sources only)
    method: str = "text_layer"


@dataclass(frozen=True)
class FakePaper:
    key: str
    title: str
    source_name: str
    source_kind: str  # "pdf" or "latex"
    authors: tuple[str, ...]
    year: int
    pages: tuple[FakePage, ...]

    @property
    def doc_id(self) -> str:
        return hashlib.sha256(f"synthetic-paper:{self.key}".encode()).hexdigest()

    @property
    def short_id(self) -> str:
        return self.doc_id[:16]

    @property
    def citation(self) -> str:
        first, *others = self.authors
        who = f"{first.split()[-1]} et al." if others else first
        return f"{who} ({self.year}). {self.title}."


TRANSMON = FakePaper(
    key="transmon",
    title="Coherence limits of fixed-frequency transmon qubits (synthetic)",
    source_name="synthetic-transmon-coherence.pdf",
    source_kind="pdf",
    authors=("N. Example", "R. Sample"),
    year=2025,
    pages=(
        FakePage(
            "# Coherence limits of fixed-frequency transmon qubits\n\n"
            "## Abstract\n\n"
            "We study what limits the energy relaxation time $T_1$ and the dephasing time "
            "$T_2$ of a fixed-frequency transmon on a sapphire substrate. Across 40 simulated "
            "devices the median $T_1$ is 110 us and the best is 310 us. Dielectric loss in the "
            "substrate-metal interface explains most of the spread, while radiative decay "
            "through the readout resonator (the Purcell effect) matters only for the strongest "
            "couplings."
        ),
        FakePage(
            "## 2. Loss channels\n\n"
            "The relaxation rate is the sum of the rates of the channels that carry energy "
            "away from the qubit:\n\n"
            "$$\\frac{1}{T_1} = \\frac{1}{T_1^{\\mathrm{diel}}} "
            "+ \\frac{1}{T_1^{\\mathrm{Purcell}}} + \\frac{1}{T_1^{\\mathrm{TLS}}}.$$\n\n"
            "Dielectric loss follows the loss tangent, $1/T_1^{\\mathrm{diel}} = \\omega\\, "
            "p_{\\mathrm{surf}} \\tan\\delta$, where $p_{\\mathrm{surf}}$ is the participation "
            "of the lossy surface. Two-level systems (TLS) in the oxide add a slowly drifting "
            "term: a qubit that crosses a TLS during a measurement loses a factor of two in "
            "$T_1$ for hours."
        ),
        FakePage(
            "## 3. Dephasing\n\n"
            "Pure dephasing adds to relaxation in the coherence time,\n\n"
            "$$\\frac{1}{T_2} = \\frac{1}{2T_1} + \\frac{1}{T_\\phi}.$$\n\n"
            "For a fixed-frequency device the main source of $T_\\phi$ is residual thermal "
            "photons in the readout resonator. An echo sequence removes the slow part of the "
            "noise: in our data $T_2^{\\mathrm{echo}}$ is 1.6 times the Ramsey $T_2^*$. We "
            "conclude that improving the substrate surface raises $T_1$, and that filtering "
            "the readout line raises $T_2$."
        ),
    ),
)

SURFACE = FakePaper(
    key="surface",
    title="Surface-code memories under circuit-level noise (synthetic)",
    source_name="synthetic-surface-code-memory.pdf",
    source_kind="pdf",
    authors=("L. Placeholder", "M. Test"),
    year=2024,
    pages=(
        FakePage(
            "# Surface-code memories under circuit-level noise\n\n"
            "## Abstract\n\n"
            "We simulate a rotated surface code of distance $d$ with depolarizing noise on "
            "every gate, idle step and measurement, decoded with minimum-weight matching. The "
            "threshold is close to 0.9% per operation. Below it, the logical error rate per "
            "round falls exponentially with the distance."
        ),
        FakePage(
            "## 2. Results\n\n"
            "Let $p$ be the physical error rate and $p_{\\mathrm{th}}$ the threshold. Below "
            "threshold the logical error rate per round behaves as\n\n"
            "$$p_L \\approx A \\left(\\frac{p}{p_{\\mathrm{th}}}\\right)^{\\lfloor (d+1)/2 "
            "\\rfloor},$$\n\n"
            "with $A \\approx 0.1$ in our fits. At $p = 0.1\\%$ the distance-5 code reaches "
            "$p_L = 3\\times 10^{-5}$ and the distance-7 code reaches $4\\times 10^{-6}$, so "
            "each step of two in the distance buys about a factor of eight."
        ),
        FakePage(
            "## 3. Overhead\n\n"
            "A rotated code of distance $d$ uses $d^2$ data qubits and $d^2 - 1$ measurement "
            "qubits. To keep the logical error of a computation of $10^{9}$ rounds below "
            "$10^{-3}$ at $p = 0.1\\%$ we need $d \\ge 13$, which is 337 physical qubits per "
            "logical qubit. A leakage-reduction step every round lowers the threshold by "
            "about a tenth of a percentage point if it is left out."
        ),
    ),
)

TELEPORT = FakePaper(
    key="teleport",
    title="A compact teleportation circuit with mid-circuit measurement (synthetic)",
    source_name="synthetic-teleportation-circuit.tex",
    source_kind="latex",
    authors=("K. Fictional", "T. Mock"),
    year=2025,
    pages=(
        FakePage(
            "# A compact teleportation circuit with mid-circuit measurement\n\n"
            "We give a three-qubit circuit that teleports the state of the top qubit to the "
            "bottom one using two mid-circuit measurements and classically controlled "
            "corrections.",
            title="Introduction",
            method="latex",
        ),
        FakePage(
            "Qubits 2 and 3 start in the Bell state $(|00\\rangle + |11\\rangle)/\\sqrt{2}$. "
            "The sender applies a CNOT from qubit 1 to qubit 2, then a Hadamard on qubit 1, "
            "and measures both. The receiver applies $X$ if the second outcome is 1 and $Z$ "
            "if the first outcome is 1.\n\n"
            "```latex\n"
            "\\begin{quantikz}\n"
            "\\lstick{$|\\psi\\rangle$} & \\ctrl{1} & \\gate{H} & \\meter{} & \\cw "
            "& \\cwbend{2} \\\\\n"
            "\\lstick{$|0\\rangle$} & \\targ{} & \\ctrl{1} & \\qw & \\meter{} & \\cwbend{1} \\\\\n"
            "\\lstick{$|0\\rangle$} & \\qw & \\targ{} & \\qw & \\qw & \\gate{X^{m_2} Z^{m_1}}\n"
            "\\end{quantikz}\n"
            "```\n\n"
            "The Bell pair itself is prepared with a Hadamard and a CNOT:\n\n"
            "```latex\n"
            "\\begin{quantikz}\n"
            "\\lstick{$|0\\rangle$} & \\gate{H} & \\ctrl{1} & \\qw \\\\\n"
            "\\lstick{$|0\\rangle$} & \\qw & \\targ{} & \\qw\n"
            "\\end{quantikz}\n"
            "```",
            title="The circuit",
            method="latex",
        ),
        FakePage(
            "The corrected output equals $|\\psi\\rangle$ for every one of the four outcomes, "
            "each of which occurs with probability $1/4$:\n\n"
            "$$|\\psi_{\\mathrm{out}}\\rangle = X^{m_2} Z^{m_1} \\, \\bigl(\\,\\text{state after "
            "outcomes } m_1 m_2\\bigr) = |\\psi\\rangle.$$\n\n"
            "The circuit uses one two-qubit gate fewer than the textbook version because the "
            "Bell pair is prepared on the hardware that holds qubits 2 and 3.",
            title="Correctness",
            method="latex",
        ),
    ),
)

GROVER = FakePaper(
    key="grover",
    title="Grover search with a noisy oracle (synthetic)",
    source_name="synthetic-grover-noisy-oracle.pdf",
    source_kind="pdf",
    authors=("P. Demo", "S. Stand-in"),
    year=2023,
    pages=(
        FakePage(
            "# Grover search with a noisy oracle\n\n"
            "## 1. Introduction\n\n"
            "Grover's algorithm finds one marked item among $N$ with $O(\\sqrt{N})$ oracle "
            "calls. After $k$ iterations the probability of measuring the marked item is "
            "$\\sin^2\\bigl((2k+1)\\theta\\bigr)$ with $\\sin\\theta = 1/\\sqrt{N}$, which is "
            "close to 1 for $k \\approx \\frac{\\pi}{4}\\sqrt{N}$."
        ),
        FakePage(
            "## 2. Noise in the oracle\n\n"
            "Suppose each oracle call fails, with probability $\\epsilon$, by applying a random "
            "phase. The success probability after $k$ iterations drops to roughly "
            "$P_k \\approx (1-\\epsilon)^k \\sin^2\\bigl((2k+1)\\theta\\bigr) + "
            "\\bigl(1-(1-\\epsilon)^k\\bigr)/N$. For $N = 2^{10}$ and $\\epsilon = 0.5\\%$ the "
            "best number of iterations is 21 instead of 25, and the best success probability "
            "falls from 0.999 to 0.82."
        ),
        FakePage(
            "## 3. Practical advice\n\n"
            "Stopping early is better than running the full count when the oracle is noisy. "
            "We recommend measuring after $k^\\ast = \\arg\\max_k P_k$ iterations and repeating "
            "the whole search a few times, which beats a longer single run once "
            "$\\epsilon \\sqrt{N} > 0.2$."
        ),
    ),
)

VQE = FakePaper(
    key="vqe",
    title="Ansatz depth and barren plateaus in variational eigensolvers (synthetic)",
    source_name="synthetic-vqe-ansatz-depth.pdf",
    source_kind="pdf",
    authors=("D. Invented", "E. Fabricated"),
    year=2024,
    pages=(
        FakePage(
            "# Ansatz depth and barren plateaus in variational eigensolvers\n\n"
            "## 1. Method\n\n"
            "A variational quantum eigensolver minimizes the energy "
            "$E(\\boldsymbol\\theta) = \\langle\\psi(\\boldsymbol\\theta)|H|"
            "\\psi(\\boldsymbol\\theta)\\rangle$ over the parameters of a circuit. We compare a "
            "hardware-efficient ansatz with a chemistry-inspired one on a four-site Heisenberg "
            "chain, for depths from 1 to 12 layers."
        ),
        FakePage(
            "## 2. Results\n\n"
            "The hardware-efficient ansatz reaches the ground energy within $10^{-3}$ at depth "
            "6 for 4 qubits, but at 12 qubits the variance of the gradient shrinks like "
            "$2^{-n}$ once the depth passes about $n$, the signature of a barren plateau. The "
            "chemistry-inspired ansatz keeps a gradient variance above $10^{-2}$ at every size "
            "we tried, at the price of 3 times as many two-qubit gates."
        ),
        FakePage(
            "## 3. Recommendation\n\n"
            "Start from a shallow, problem-shaped ansatz and add layers only while the energy "
            "keeps falling. Initializing the parameters near zero, so that the circuit starts "
            "close to the identity, avoided the plateau in all our runs with up to 16 qubits."
        ),
    ),
)

CROSS_RESONANCE = FakePaper(
    key="cross-resonance",
    title="Calibrating cross-resonance gates with echo sequences (synthetic)",
    source_name="synthetic-cross-resonance-echo.pdf",
    source_kind="pdf",
    authors=("G. Notreal", "H. Madeup"),
    year=2025,
    pages=(
        FakePage(
            "# Calibrating cross-resonance gates with echo sequences\n\n"
            "## 1. Introduction\n\n"
            "The cross-resonance gate drives a control qubit at the frequency of its "
            "neighbour. The effective Hamiltonian contains the wanted $ZX$ term and unwanted "
            "$IX$, $ZI$ and $ZZ$ terms. An echoed sequence with a $\\pi$ pulse on the control "
            "cancels $IX$ and $ZI$ to first order."
        ),
        FakePage(
            "## 2. Calibration\n\n"
            "We calibrate the amplitude so that the $ZX$ rotation angle is $\\pi/2$, "
            "$\\int \\Omega_{ZX}(t)\\,dt = \\pi/2$, and the phase of the target drive so that "
            "the residual $ZY$ term vanishes. Over 30 days the two-qubit gate error stayed "
            "between 0.6% and 1.1%, and a recalibration took 4 minutes."
        ),
    ),
)

PAPERS: tuple[FakePaper, ...] = (TRANSMON, SURFACE, TELEPORT, GROVER, VQE, CROSS_RESONANCE)
