from dataclasses import dataclass

from station.audit.provenance import ProvenanceError, ResolvedProvenance, resolve_provenance
from station.audit.recompute import recompute_report
from station.audit.verification import VerificationError, verify_report
from station.intake.store import EvidenceStore
from station.report.archive import ReportArchive, SignedReport
from station.report.handover import HandoverStore
from station.setup.shifts import ShiftRegistry
from station.setup.storage import StationDatabase


@dataclass(frozen=True)
class ReadbackResult:
    report: SignedReport
    provenance: ResolvedProvenance


def readback(database: StationDatabase, report_number: str) -> ReadbackResult:
    if not isinstance(report_number, str) or not report_number.strip():
        raise VerificationError('report number must be a nonempty identifier')
    with database.read_snapshot() as conn:
        try:
            archive = ReportArchive(conn)
            report = archive.find_by_number(report_number)
            if report is not None:
                # Local station reports resolve against the station's own intake.
                store = EvidenceStore(conn)
            else:
                # Accepted external reports keep their source originals and receipts.
                handover_store = HandoverStore(conn)
                report = handover_store.find_report_by_number(report_number)
                if report is None:
                    raise VerificationError(f'unknown report {report_number!r}')
                store = handover_store
            provenance = resolve_provenance(report, store)
            shift = ShiftRegistry(conn).find(report.identity.shift_id)
        except (ProvenanceError, VerificationError):
            raise
        except ValueError as error:
            raise VerificationError(f'invalid archive record: {error}') from error
        if shift is None:
            raise VerificationError('missing frozen shift')
        recomputed = recompute_report(shift, provenance)
        verify_report(report, report_number, shift, recomputed)
        return ReadbackResult(report, provenance)
