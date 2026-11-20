from dataclasses import dataclass

from station.intake.store import EvidenceStore, StoredEvidence
from station.report.archive import SignedReport


class ProvenanceError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedProvenance:
    entries: tuple[StoredEvidence, ...]


def resolve_provenance(report: SignedReport, store: EvidenceStore) -> ResolvedProvenance:
    recording_id = report.identity.recording_id
    entries = []
    for position, ref in enumerate(report.references, start=1):
        if ref.recording_id != recording_id or ref.sequence != position:
            raise ProvenanceError(f'invalid recording or sequence at reference {position}')
        item = store.by_sequence(ref.recording_id, ref.sequence)
        if item is None:
            raise ProvenanceError(f'missing evidence or receipt for recording {ref.recording_id} sequence {ref.sequence}')
        evidence, receipt = item.evidence, item.receipt
        if (evidence.shift_id, evidence.device_id, evidence.action_id) != (
            report.identity.shift_id, report.shift.device_id, ref.action_id
        ):
            raise ProvenanceError(f'evidence binding mismatch at reference {position}')
        if (receipt.receipt_id, receipt.recording_id, receipt.sequence, receipt.action_id) != (
            ref.receipt_id, ref.recording_id, ref.sequence, ref.action_id
        ):
            raise ProvenanceError(f'receipt identity mismatch at reference {position}')
        last_reference = position == len(report.references)
        if last_reference:
            if evidence.kind != 'stop' or evidence.terminal_sequence != position:
                raise ProvenanceError('last reference does not establish the stop boundary')
        elif evidence.kind != 'inspection' or evidence.terminal_sequence is not None:
            raise ProvenanceError(f'invalid inspection at reference {position}')
        entries.append(item)
    if not entries:
        raise ProvenanceError('report has no evidence references')
    return ResolvedProvenance(tuple(entries))
