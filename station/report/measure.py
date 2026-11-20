from collections.abc import Iterable
from dataclasses import dataclass

from station.intake.evidence import AcceptedEvidence
from station.setup.shifts import ShiftSnapshot


@dataclass(frozen=True)
class ShiftTotals:
    pulses: int
    microliters: int


def measure_shift(shift: ShiftSnapshot, evidence: Iterable[AcceptedEvidence]) -> ShiftTotals:
    pulses = sum(item.pulses for item in evidence)
    return ShiftTotals(pulses, pulses * shift.microliters_per_pulse)
