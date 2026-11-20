from dataclasses import dataclass

from station.intake.evidence import AcceptedEvidence
from station.intake.receipts import Receipt
from station.setup.storage import decode_quantity, encode_quantity


INTAKE_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS evidence (
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
    '''CREATE UNIQUE INDEX IF NOT EXISTS one_terminal_per_recording
       ON evidence (recording_id) WHERE kind = 'stop' ''',
    '''CREATE TABLE IF NOT EXISTS receipts (
        recording_id TEXT NOT NULL,
        sequence TEXT NOT NULL,
        receipt_id TEXT NOT NULL UNIQUE,
        action_id TEXT NOT NULL,
        PRIMARY KEY (recording_id, sequence),
        FOREIGN KEY (recording_id, sequence) REFERENCES evidence (recording_id, sequence)
    )''',
)


@dataclass(frozen=True)
class StoredEvidence:
    evidence: AcceptedEvidence
    receipt: Receipt


_SELECT = '''SELECT e.*, r.receipt_id, r.action_id AS receipt_action_id
             FROM evidence AS e JOIN receipts AS r
             ON r.recording_id = e.recording_id AND r.sequence = e.sequence'''


def _stored(row):
    if row is None:
        return None
    sequence = decode_quantity(row['sequence'])
    terminal = row['terminal_sequence']
    evidence = AcceptedEvidence(row['shift_id'], row['device_id'], row['recording_id'],
                                sequence, row['action_id'], row['kind'], decode_quantity(row['pulses']),
                                None if terminal is None else decode_quantity(terminal))
    receipt = Receipt(row['receipt_id'], row['recording_id'], sequence, row['receipt_action_id'])
    return StoredEvidence(evidence, receipt)


class EvidenceStore:
    def __init__(self, conn):
        self.conn = conn

    def by_sequence(self, recording_id, sequence):
        row = self.conn.execute(
            _SELECT + ' WHERE e.recording_id = ? AND e.sequence = ?',
            (recording_id, encode_quantity(sequence)),
        ).fetchone()
        return _stored(row)

    def by_action(self, recording_id, action_id):
        row = self.conn.execute(
            _SELECT + ' WHERE e.recording_id = ? AND e.action_id = ?', (recording_id, action_id)
        ).fetchone()
        return _stored(row)

    def list_for_recording(self, recording_id):
        # Canonical hexadecimal strings sort numerically by length, then by value.
        rows = self.conn.execute(
            _SELECT + ' WHERE e.recording_id = ? ORDER BY length(e.sequence), e.sequence',
            (recording_id,),
        )
        return tuple(_stored(row) for row in rows)

    def terminal(self, recording_id):
        row = self.conn.execute(
            _SELECT + " WHERE e.recording_id = ? AND e.kind = 'stop'", (recording_id,)
        ).fetchone()
        return _stored(row)

    def append(self, evidence: AcceptedEvidence, receipt: Receipt):
        sequence = encode_quantity(evidence.sequence)
        terminal = evidence.terminal_sequence
        self.conn.execute(
            '''INSERT INTO evidence (recording_id, sequence, shift_id, device_id, action_id,
                                     kind, pulses, terminal_sequence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (evidence.recording_id, sequence, evidence.shift_id, evidence.device_id,
             evidence.action_id, evidence.kind, encode_quantity(evidence.pulses),
             None if terminal is None else encode_quantity(terminal)),
        )
        self.conn.execute(
            '''INSERT INTO receipts (recording_id, sequence, receipt_id, action_id)
               VALUES (?, ?, ?, ?)''',
            (receipt.recording_id, encode_quantity(receipt.sequence), receipt.receipt_id, receipt.action_id),
        )
