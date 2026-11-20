"""Offline handover of signed shift reports between two station archives.

A handover package is a JSON-round-trippable dictionary carrying the source
station's signed report together with every original evidence item and the
source station's first receipts.  The receiving station must already hold the
same frozen opening snapshot; import only stores the package when the whole
package verifies and the single SQLite transaction commits.
"""

from dataclasses import dataclass

from station.intake.evidence import AcceptedEvidence
from station.intake.receipts import Receipt
from station.intake.store import StoredEvidence
from station.report.archive import ReportArchive, ReportEvidenceRef, SignedReport
from station.report.identity import ReportIdentity
from station.report.limits import LimitDecision, decide_limit
from station.report.measure import ShiftTotals, measure_shift
from station.setup.shifts import ShiftRegistry, ShiftSnapshot
from station.setup.storage import StationDatabase, decode_quantity, encode_quantity


HANDOVER_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS handover_reports (
        report_number TEXT PRIMARY KEY NOT NULL,
        shift_id TEXT NOT NULL UNIQUE REFERENCES shifts (shift_id),
        recording_id TEXT NOT NULL UNIQUE REFERENCES shifts (recording_id),
        device_id TEXT NOT NULL REFERENCES devices (device_id),
        revision TEXT NOT NULL,
        microliters_per_pulse TEXT NOT NULL,
        limit_microliters TEXT NOT NULL,
        pulses TEXT NOT NULL,
        microliters TEXT NOT NULL,
        over_limit INTEGER NOT NULL CHECK (over_limit IN (0, 1))
    )''',
    '''CREATE TABLE IF NOT EXISTS handover_evidence (
        recording_id TEXT NOT NULL REFERENCES shifts (recording_id),
        sequence TEXT NOT NULL,
        shift_id TEXT NOT NULL REFERENCES shifts (shift_id),
        device_id TEXT NOT NULL REFERENCES devices (device_id),
        action_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        pulses TEXT NOT NULL,
        terminal_sequence TEXT,
        PRIMARY KEY (recording_id, sequence),
        UNIQUE (recording_id, action_id),
        CHECK ((kind = 'inspection' AND terminal_sequence IS NULL) OR
               (kind = 'stop' AND terminal_sequence IS NOT NULL AND terminal_sequence = sequence))
    )''',
    '''CREATE UNIQUE INDEX IF NOT EXISTS handover_one_terminal
       ON handover_evidence (recording_id) WHERE kind = 'stop' ''',
    '''CREATE TABLE IF NOT EXISTS handover_receipts (
        recording_id TEXT NOT NULL,
        sequence TEXT NOT NULL,
        receipt_id TEXT NOT NULL UNIQUE,
        action_id TEXT NOT NULL,
        PRIMARY KEY (recording_id, sequence),
        FOREIGN KEY (recording_id, sequence)
            REFERENCES handover_evidence (recording_id, sequence)
    )''',
    '''CREATE TABLE IF NOT EXISTS handover_refs (
        report_number TEXT NOT NULL REFERENCES handover_reports (report_number),
        position INTEGER NOT NULL CHECK (position > 0),
        recording_id TEXT NOT NULL,
        sequence TEXT NOT NULL,
        action_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        PRIMARY KEY (report_number, position),
        UNIQUE (report_number, recording_id, sequence),
        FOREIGN KEY (recording_id, sequence)
            REFERENCES handover_evidence (recording_id, sequence),
        FOREIGN KEY (recording_id, sequence)
            REFERENCES handover_receipts (recording_id, sequence)
    )''',
)


@dataclass(frozen=True)
class DecodedHandover:
    report: SignedReport
    entries: tuple[StoredEvidence, ...]


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def _encode_evidence(evidence: AcceptedEvidence) -> dict:
    return {
        'shift_id': evidence.shift_id,
        'device_id': evidence.device_id,
        'recording_id': evidence.recording_id,
        'sequence': encode_quantity(evidence.sequence),
        'action_id': evidence.action_id,
        'kind': evidence.kind,
        'pulses': encode_quantity(evidence.pulses),
        'terminal_sequence': (
            None if evidence.terminal_sequence is None
            else encode_quantity(evidence.terminal_sequence)
        ),
    }


def export_handover(database: StationDatabase, report_number: str) -> dict:
    """Export a signed source report with its checked original evidence."""
    # Imported lazily so the audit package can reuse HandoverStore without an
    # import cycle between station.report and station.audit.
    from station.audit.readback import readback

    result = readback(database, report_number)
    report = result.report
    package_report = {
        'identity': {
            'number': report.identity.number,
            'shift_id': report.identity.shift_id,
            'recording_id': report.identity.recording_id,
        },
        'shift': {
            'shift_id': report.shift.shift_id,
            'device_id': report.shift.device_id,
            'recording_id': report.shift.recording_id,
            'revision': report.shift.revision,
            'microliters_per_pulse': encode_quantity(report.shift.microliters_per_pulse),
            'limit_microliters': encode_quantity(report.shift.limit_microliters),
        },
        'totals': {
            'pulses': encode_quantity(report.totals.pulses),
            'microliters': encode_quantity(report.totals.microliters),
        },
        'decision': {
            'limit_microliters': encode_quantity(report.decision.limit_microliters),
            'over_limit': bool(report.decision.over_limit),
        },
        'references': [
            {
                'recording_id': ref.recording_id,
                'sequence': encode_quantity(ref.sequence),
                'action_id': ref.action_id,
                'receipt_id': ref.receipt_id,
            }
            for ref in report.references
        ],
    }
    entries = [
        {
            'evidence': _encode_evidence(item.evidence),
            'receipt': {
                'receipt_id': item.receipt.receipt_id,
                'recording_id': item.receipt.recording_id,
                'sequence': encode_quantity(item.receipt.sequence),
                'action_id': item.receipt.action_id,
            },
        }
        for item in result.provenance.entries
    ]
    return {'report': package_report, 'entries': entries}


# --------------------------------------------------------------------------- #
# Package decoding and validation
# --------------------------------------------------------------------------- #

def _require_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f'invalid handover package: {label} must be an object with keys {sorted(keys)}')
    return value


def _require_identifier(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'invalid handover package: {label} must be a nonblank identifier')
    return value


def _require_quantity(value, label):
    if not isinstance(value, str):
        raise ValueError(f'invalid handover package: {label} must be a hexadecimal quantity string')
    try:
        return decode_quantity(value)
    except ValueError as error:
        raise ValueError(f'invalid handover package: {label} {error}') from error


def decode_handover(package) -> DecodedHandover:
    """Parse a handover package and verify every internal cross-reference."""
    if not isinstance(package, dict) or set(package) != {'report', 'entries'}:
        raise ValueError('invalid handover package: root must contain report and entries')

    report_part = _require_keys(
        package['report'],
        ('identity', 'shift', 'totals', 'decision', 'references'),
        'report',
    )
    identity = _require_keys(
        report_part['identity'], ('number', 'shift_id', 'recording_id'), 'identity'
    )
    shift_part = _require_keys(
        report_part['shift'],
        ('shift_id', 'device_id', 'recording_id', 'revision',
         'microliters_per_pulse', 'limit_microliters'),
        'shift',
    )
    totals_part = _require_keys(report_part['totals'], ('pulses', 'microliters'), 'totals')
    decision_part = _require_keys(
        report_part['decision'], ('limit_microliters', 'over_limit'), 'decision'
    )

    number = _require_identifier(identity['number'], 'report number')
    shift_id = _require_identifier(identity['shift_id'], 'shift_id')
    recording_id = _require_identifier(identity['recording_id'], 'recording_id')
    device_id = _require_identifier(shift_part['device_id'], 'device_id')
    revision = _require_identifier(shift_part['revision'], 'revision')
    _require_identifier(shift_part['shift_id'], 'shift shift_id')
    _require_identifier(shift_part['recording_id'], 'shift recording_id')
    if (shift_part['shift_id'], shift_part['recording_id']) != (shift_id, recording_id):
        raise ValueError('invalid handover package: shift binding does not match report identity')

    coefficient = _require_quantity(shift_part['microliters_per_pulse'], 'microliters_per_pulse')
    if coefficient <= 0:
        raise ValueError('invalid handover package: microliters_per_pulse must be positive')
    frozen_limit = _require_quantity(shift_part['limit_microliters'], 'shift limit_microliters')

    pulses_total = _require_quantity(totals_part['pulses'], 'totals pulses')
    microliters_total = _require_quantity(totals_part['microliters'], 'totals microliters')
    decision_limit = _require_quantity(decision_part['limit_microliters'], 'decision limit_microliters')
    if decision_limit != frozen_limit:
        raise ValueError('invalid handover package: limit decisions disagree')
    if not isinstance(decision_part['over_limit'], bool):
        raise ValueError('invalid handover package: over_limit must be a JSON boolean')
    over_limit = decision_part['over_limit']

    raw_references = report_part['references']
    raw_entries = package['entries']
    if not isinstance(raw_references, list) or not isinstance(raw_entries, list):
        raise ValueError('invalid handover package: references and entries must be arrays')
    if not raw_references or len(raw_references) != len(raw_entries):
        raise ValueError('invalid handover package: references and entries must be equally sized nonempty arrays')

    shift = ShiftSnapshot(shift_id, device_id, recording_id, revision, coefficient, frozen_limit)
    entries = []
    references = []
    seen_actions = set()
    seen_receipts = set()
    terminal_sequence = None

    for position, (raw_ref, raw_entry) in enumerate(zip(raw_references, raw_entries), start=1):
        ref = _require_keys(
            raw_ref, ('recording_id', 'sequence', 'action_id', 'receipt_id'), f'reference {position}'
        )
        entry = _require_keys(raw_entry, ('evidence', 'receipt'), f'entry {position}')
        evidence_part = _require_keys(
            entry['evidence'],
            ('shift_id', 'device_id', 'recording_id', 'sequence', 'action_id',
             'kind', 'pulses', 'terminal_sequence'),
            f'evidence {position}',
        )
        receipt_part = _require_keys(
            entry['receipt'], ('receipt_id', 'recording_id', 'sequence', 'action_id'),
            f'receipt {position}',
        )

        ref_sequence = _require_quantity(ref['sequence'], f'reference {position} sequence')
        entry_sequence = _require_quantity(
            evidence_part['sequence'], f'evidence {position} sequence'
        )
        receipt_sequence = _require_quantity(
            receipt_part['sequence'], f'receipt {position} sequence'
        )
        if not (ref_sequence == entry_sequence == receipt_sequence == position):
            raise ValueError(f'invalid handover package: sequence mismatch at position {position}')
        for bound_part, label in ((ref, f'reference {position}'),
                                  (evidence_part, f'evidence {position}'),
                                  (receipt_part, f'receipt {position}')):
            if bound_part['recording_id'] != recording_id:
                raise ValueError(f'invalid handover package: {label} is bound to another recording')

        if (evidence_part['shift_id'], evidence_part['device_id']) != (shift_id, device_id):
            raise ValueError(f'invalid handover package: evidence {position} breaks the frozen shift binding')
        action_id = _require_identifier(ref['action_id'], f'reference {position} action_id')
        _require_identifier(evidence_part['action_id'], f'evidence {position} action_id')
        _require_identifier(receipt_part['action_id'], f'receipt {position} action_id')
        if not (ref['action_id'] == evidence_part['action_id'] == receipt_part['action_id'] == action_id):
            raise ValueError(f'invalid handover package: action_id mismatch at position {position}')
        if action_id in seen_actions:
            raise ValueError(f'invalid handover package: duplicate action_id {action_id!r}')
        seen_actions.add(action_id)

        receipt_id = _require_identifier(ref['receipt_id'], f'reference {position} receipt_id')
        _require_identifier(receipt_part['receipt_id'], f'receipt {position} receipt_id')
        if ref['receipt_id'] != receipt_part['receipt_id'] or receipt_id in seen_receipts:
            raise ValueError(
                f'invalid handover package: receipt identity mismatch or duplicate at position {position}'
            )
        seen_receipts.add(receipt_id)

        kind = evidence_part['kind']
        if kind not in ('inspection', 'stop'):
            raise ValueError(f'invalid handover package: unknown evidence kind at position {position}')
        pulses = _require_quantity(evidence_part['pulses'], f'evidence {position} pulses')
        raw_terminal = evidence_part['terminal_sequence']
        if kind == 'inspection':
            if raw_terminal is not None:
                raise ValueError(
                    f'invalid handover package: inspection at position {position} carries a terminal sequence'
                )
            item_terminal = None
        else:
            if raw_terminal is None:
                raise ValueError(f'invalid handover package: stop at position {position} needs a terminal sequence')
            item_terminal = _require_quantity(raw_terminal, f'evidence {position} terminal_sequence')
            if item_terminal != position:
                raise ValueError('invalid handover package: stop terminal sequence must equal its position')
            if terminal_sequence is not None:
                raise ValueError('invalid handover package: more than one stop item')
            terminal_sequence = position

        evidence = AcceptedEvidence(shift_id, device_id, recording_id, position, action_id,
                                    kind, pulses, item_terminal)
        receipt = Receipt(receipt_id, recording_id, position, action_id)
        entries.append(StoredEvidence(evidence, receipt))
        references.append(ReportEvidenceRef(recording_id, position, action_id, receipt_id))

    if terminal_sequence != len(entries):
        raise ValueError('invalid handover package: the last item must establish the stop boundary')

    expected_totals = measure_shift(shift, (item.evidence for item in entries))
    if expected_totals.pulses != pulses_total:
        raise ValueError('invalid handover package: pulse total does not match the original codes')
    if expected_totals.microliters != microliters_total:
        raise ValueError('invalid handover package: microliter total does not match the frozen coefficient')
    expected_decision = decide_limit(microliters_total, frozen_limit)
    if expected_decision.over_limit != over_limit:
        raise ValueError('invalid handover package: limit decision does not match the exact measurement')

    report = SignedReport(
        identity=ReportIdentity(number, shift_id, recording_id),
        shift=shift,
        totals=expected_totals,
        decision=expected_decision,
        references=tuple(references),
    )
    return DecodedHandover(report, tuple(entries))


# --------------------------------------------------------------------------- #
# Receiving-side storage
# --------------------------------------------------------------------------- #

_HANDOVER_SELECT = '''SELECT e.*, r.receipt_id, r.action_id AS receipt_action_id
                      FROM handover_evidence AS e JOIN handover_receipts AS r
                      ON r.recording_id = e.recording_id AND r.sequence = e.sequence'''


def _handover_row(row) -> StoredEvidence:
    sequence = decode_quantity(row['sequence'])
    terminal = row['terminal_sequence']
    evidence = AcceptedEvidence(
        row['shift_id'], row['device_id'], row['recording_id'], sequence,
        row['action_id'], row['kind'], decode_quantity(row['pulses']),
        None if terminal is None else decode_quantity(terminal),
    )
    receipt = Receipt(row['receipt_id'], row['recording_id'], sequence, row['receipt_action_id'])
    return StoredEvidence(evidence, receipt)


class HandoverStore:
    """The imported originals and source receipts kept beside local intake."""

    def __init__(self, conn):
        self.conn = conn

    def find_report_by_number(self, report_number):
        row = self.conn.execute(
            'SELECT * FROM handover_reports WHERE report_number = ?', (report_number,)
        ).fetchone()
        return self._load_report(row)

    def find_report_by_recording(self, recording_id):
        row = self.conn.execute(
            'SELECT * FROM handover_reports WHERE recording_id = ?', (recording_id,)
        ).fetchone()
        return self._load_report(row)

    def _load_report(self, row):
        if row is None:
            return None
        identity = ReportIdentity(row['report_number'], row['shift_id'], row['recording_id'])
        shift = ShiftSnapshot(
            row['shift_id'], row['device_id'], row['recording_id'], row['revision'],
            decode_quantity(row['microliters_per_pulse']), decode_quantity(row['limit_microliters']),
        )
        totals = ShiftTotals(decode_quantity(row['pulses']), decode_quantity(row['microliters']))
        decision = LimitDecision(shift.limit_microliters, bool(row['over_limit']))
        ref_rows = self.conn.execute(
            'SELECT * FROM handover_refs WHERE report_number = ? ORDER BY position',
            (identity.number,),
        )
        references = tuple(
            ReportEvidenceRef(ref['recording_id'], decode_quantity(ref['sequence']),
                              ref['action_id'], ref['receipt_id'])
            for ref in ref_rows
        )
        return SignedReport(identity, shift, totals, decision, references)

    def by_sequence(self, recording_id, sequence):
        row = self.conn.execute(
            _HANDOVER_SELECT + ' WHERE e.recording_id = ? AND e.sequence = ?',
            (recording_id, encode_quantity(sequence)),
        ).fetchone()
        return None if row is None else _handover_row(row)

    def by_action(self, recording_id, action_id):
        row = self.conn.execute(
            _HANDOVER_SELECT + ' WHERE e.recording_id = ? AND e.action_id = ?',
            (recording_id, action_id),
        ).fetchone()
        return None if row is None else _handover_row(row)

    def terminal(self, recording_id):
        row = self.conn.execute(
            _HANDOVER_SELECT + " WHERE e.recording_id = ? AND e.kind = 'stop'",
            (recording_id,),
        ).fetchone()
        return None if row is None else _handover_row(row)

    def list_for_recording(self, recording_id):
        rows = self.conn.execute(
            _HANDOVER_SELECT + ' WHERE e.recording_id = ? ORDER BY length(e.sequence), e.sequence',
            (recording_id,),
        )
        return tuple(_handover_row(row) for row in rows)

    def append_report(self, decoded: DecodedHandover) -> None:
        report = decoded.report
        identity, shift = report.identity, report.shift
        self.conn.execute(
            '''INSERT INTO handover_reports
                   (report_number, shift_id, recording_id, device_id, revision,
                    microliters_per_pulse, limit_microliters, pulses, microliters, over_limit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (identity.number, identity.shift_id, identity.recording_id, shift.device_id,
             shift.revision, encode_quantity(shift.microliters_per_pulse),
             encode_quantity(shift.limit_microliters), encode_quantity(report.totals.pulses),
             encode_quantity(report.totals.microliters), int(report.decision.over_limit)),
        )
        for item in decoded.entries:
            evidence, receipt = item.evidence, item.receipt
            terminal = evidence.terminal_sequence
            self.conn.execute(
                '''INSERT INTO handover_evidence
                       (recording_id, sequence, shift_id, device_id, action_id,
                        kind, pulses, terminal_sequence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (evidence.recording_id, encode_quantity(evidence.sequence), evidence.shift_id,
                 evidence.device_id, evidence.action_id, evidence.kind,
                 encode_quantity(evidence.pulses),
                 None if terminal is None else encode_quantity(terminal)),
            )
            self.conn.execute(
                '''INSERT INTO handover_receipts (recording_id, sequence, receipt_id, action_id)
                   VALUES (?, ?, ?, ?)''',
                (receipt.recording_id, encode_quantity(receipt.sequence),
                 receipt.receipt_id, receipt.action_id),
            )
        self.conn.executemany(
            '''INSERT INTO handover_refs
                   (report_number, position, recording_id, sequence, action_id, receipt_id)
               VALUES (?, ?, ?, ?, ?, ?)''',
            ((identity.number, position, ref.recording_id, encode_quantity(ref.sequence),
              ref.action_id, ref.receipt_id)
             for position, ref in enumerate(report.references, start=1)),
        )


def _check_local_intake(conn, decoded: DecodedHandover) -> None:
    """Reject when local intake already conflicts with the incoming boundary."""
    from station.intake.store import EvidenceStore

    recording_id = decoded.report.identity.recording_id
    terminal = next(item for item in decoded.entries if item.evidence.kind == 'stop')
    last_sequence = terminal.evidence.terminal_sequence
    local_store = EvidenceStore(conn)
    local_terminal = local_store.terminal(recording_id)
    if local_terminal is not None and local_terminal.evidence.terminal_sequence != last_sequence:
        raise ValueError('handover conflicts with the local stop boundary')
    incoming = {item.evidence.sequence: item.evidence for item in decoded.entries}
    for local in local_store.list_for_recording(recording_id):
        if local.evidence.sequence > last_sequence:
            raise ValueError('local evidence already extends beyond the handover stop boundary')
        original = incoming.get(local.evidence.sequence)
        if original is None or local.evidence != original:
            raise ValueError('local evidence content conflicts with the handover original')


def import_handover(database: StationDatabase, package) -> str:
    """Accept a complete handover package into a writable station database."""
    decoded = decode_handover(package)
    report = decoded.report
    identity = report.identity
    with database.transaction('import_handover') as conn:
        shift = ShiftRegistry(conn).find(identity.shift_id)
        if shift is None:
            raise LookupError(f'unknown shift {identity.shift_id!r}')
        if shift != report.shift:
            raise ValueError('handover package does not match the frozen opening snapshot')

        handover_store = HandoverStore(conn)
        existing = handover_store.find_report_by_number(identity.number)
        if existing is not None:
            if existing != report:
                raise ValueError('report number is already used by another handover report')
            stored_entries = handover_store.list_for_recording(identity.recording_id)
            if stored_entries != decoded.entries:
                raise ValueError('handover package conflicts with the previously accepted package')
            return identity.number

        if ReportArchive(conn).find_by_number(identity.number) is not None:
            raise ValueError('report number is already used by a local report')
        if handover_store.find_report_by_recording(identity.recording_id) is not None:
            raise ValueError('the recording already carries another handover report')

        _check_local_intake(conn, decoded)
        handover_store.append_report(decoded)
        return identity.number
