from dataclasses import dataclass

from station.intake.completeness import read_close_evidence
from station.intake.store import EvidenceStore
from station.report.archive import ReportArchive, ReportEvidenceRef, SignedReport
from station.report.identity import new_report_identity
from station.report.limits import decide_limit
from station.report.measure import measure_shift
from station.setup.shifts import ShiftRegistry
from station.setup.storage import StationDatabase


@dataclass(frozen=True)
class PublishOutcome:
    status: str
    missing_sequences: tuple[int, ...] = ()
    report: SignedReport | None = None


def publish(database: StationDatabase, shift_id: str) -> PublishOutcome:
    with database.transaction('publish') as conn:
        shift = ShiftRegistry(conn).find(shift_id)
        if shift is None:
            raise LookupError(f'unknown shift {shift_id!r}')
        archive = ReportArchive(conn)
        original = archive.find_by_shift(shift_id)
        if original is not None:
            return PublishOutcome('signed', report=original)
        close = read_close_evidence(EvidenceStore(conn), shift.recording_id)
        if close.terminal_sequence is None:
            return PublishOutcome('awaiting_stop')
        if close.missing_sequences:
            return PublishOutcome('missing', close.missing_sequences)
        totals = measure_shift(shift, (entry.evidence for entry in close.entries))
        decision = decide_limit(totals.microliters, shift.limit_microliters)
        identity = new_report_identity(shift.shift_id, shift.recording_id)
        references = tuple(
            ReportEvidenceRef(entry.evidence.recording_id, entry.evidence.sequence,
                              entry.evidence.action_id, entry.receipt.receipt_id)
            for entry in close.entries
        )
        report = SignedReport(identity, shift, totals, decision, references)
        archive.insert(report)
        return PublishOutcome('signed', report=report)
