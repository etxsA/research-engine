from collections import deque

from docingest.entrypoints.interactive import InteractiveController
from quantum_agent.engine import StatevectorSimulator


class ScriptedTerminal:
    def __init__(self, answers):
        self.answers = deque(answers)
        self.output = []

    def ask(self, prompt):
        self.output.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.popleft()

    def choose(self, prompt, choices):
        self.output.append(prompt)
        return self.ask("Choice")

    def write(self, message=""):
        self.output.append(message)


def test_quantum_menu_runs_bell_and_returns_to_main_menu():
    io = ScriptedTerminal(["2", "3", "Bell", "0", "0"])
    InteractiveController(io, StatevectorSimulator()).run()
    assert any("Step 1: H" in message for message in io.output)
    assert any("|00>" in message and "|11>" in message for message in io.output)


def test_invalid_main_menu_choice_is_recoverable():
    io = ScriptedTerminal(["invalid", "0"])
    InteractiveController(io, StatevectorSimulator()).run()
    assert any("Invalid choice" in message for message in io.output)
    assert any("Document paths may be absolute or relative" in message for message in io.output)


def test_quantum_menu_prints_input_quick_guide():
    io = ScriptedTerminal(["2", "0", "0"])
    InteractiveController(io, StatevectorSimulator()).run()
    assert any("vectors as `1,0` or `|0>`" in message for message in io.output)
    assert any("branch `00`, `01`, `10`, or `11`" in message for message in io.output)


def test_single_qubit_custom_gate_reports_validation_error():
    io = ScriptedTerminal(["2", "1", "1,0", "custom", "1,1;1,1", "0", "0"])
    InteractiveController(io, StatevectorSimulator()).run()
    assert any("not unitary" in message for message in io.output)


def test_tensor_vector_operation_produces_normalized_result():
    io = ScriptedTerminal(["2", "2", "1,0", "0,1", "v", "0", "0"])
    InteractiveController(io, StatevectorSimulator()).run()
    assert any("tensor" in message and "1." in message for message in io.output)
