from dataclasses import dataclass

from station.audit.provenance import ResolvedProvenance
from station.report.limits import LimitDecision, decide_limit
from station.report.measure import ShiftTotals, measure_shift
from station.setup.shifts import ShiftSnapshot


@dataclass(frozen=True)
class RecomputedReport:
    totals: ShiftTotals
    decision: LimitDecision


def recompute_report(shift: ShiftSnapshot, provenance: ResolvedProvenance) -> RecomputedReport:
    totals = measure_shift(shift, (item.evidence for item in provenance.entries))
    return RecomputedReport(totals, decide_limit(totals.microliters, shift.limit_microliters))
