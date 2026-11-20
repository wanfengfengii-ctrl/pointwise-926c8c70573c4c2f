from station.intake.evidence import same_evidence, validate_source_evidence
from station.intake.receipts import Receipt, make_receipt
from station.intake.store import EvidenceStore
from station.meter.actions import SourceEvidence
from station.report.handover import HandoverStore
from station.setup.shifts import ShiftRegistry
from station.setup.storage import StationDatabase


def _check_terminal_range(store, evidence):
    terminal = store.terminal(evidence.recording_id)
    if terminal is not None:
        if evidence.kind == 'stop' or evidence.sequence >= terminal.evidence.sequence:
            raise ValueError('evidence conflicts with the received stop boundary')
    elif evidence.kind == 'stop':
        existing = store.list_for_recording(evidence.recording_id)
        if existing and existing[-1].evidence.sequence > evidence.sequence:
            raise ValueError('stop boundary excludes previously received evidence')


def _check_handover_boundary(conn, evidence):
    """Local intake must match imported originals and share their stop edge."""
    handover = HandoverStore(conn)
    terminal = handover.terminal(evidence.recording_id)
    if terminal is None:
        return
    if evidence.sequence > terminal.evidence.terminal_sequence:
        raise ValueError('evidence conflicts with the imported stop boundary')
    same_position = handover.by_sequence(evidence.recording_id, evidence.sequence)
    same_action = handover.by_action(evidence.recording_id, evidence.action_id)
    if same_position is None or same_action is None or same_position != same_action:
        raise ValueError('evidence identity conflicts with the imported originals')
    if not same_evidence(same_position.evidence, evidence):
        raise ValueError('evidence content conflicts with the imported originals')


def accept(database: StationDatabase, source: SourceEvidence) -> Receipt:
    if not isinstance(source, SourceEvidence):
        raise ValueError('expected a source evidence record')
    with database.transaction('accept') as conn:
        shift = ShiftRegistry(conn).find(source.shift_id)
        if shift is None:
            raise LookupError(f'unknown shift {source.shift_id!r}')
        evidence = validate_source_evidence(source, shift)
        store = EvidenceStore(conn)
        by_sequence = store.by_sequence(evidence.recording_id, evidence.sequence)
        by_action = store.by_action(evidence.recording_id, evidence.action_id)
        if by_sequence is not None or by_action is not None:
            if by_sequence is None or by_sequence != by_action or not same_evidence(by_sequence.evidence, evidence):
                raise ValueError('evidence identity or content conflict')
            return by_sequence.receipt
        _check_handover_boundary(conn, evidence)
        _check_terminal_range(store, evidence)
        receipt = make_receipt(evidence)
        store.append(evidence, receipt)
        return receipt
