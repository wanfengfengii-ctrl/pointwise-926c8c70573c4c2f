from dataclasses import dataclass

from station.report.identity import ReportIdentity
from station.report.limits import LimitDecision
from station.report.measure import ShiftTotals
from station.setup.shifts import ShiftSnapshot
from station.setup.storage import decode_quantity, encode_quantity


REPORT_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS reports (
        report_number TEXT PRIMARY KEY NOT NULL,
        shift_id TEXT NOT NULL UNIQUE REFERENCES shifts (shift_id),
        recording_id TEXT NOT NULL REFERENCES shifts (recording_id),
        device_id TEXT NOT NULL REFERENCES devices (device_id),
        revision TEXT NOT NULL,
        microliters_per_pulse TEXT NOT NULL,
        limit_microliters TEXT NOT NULL,
        pulses TEXT NOT NULL,
        microliters TEXT NOT NULL,
        over_limit INTEGER NOT NULL CHECK (over_limit IN (0, 1))
    )''',
    '''CREATE TABLE IF NOT EXISTS report_refs (
        report_number TEXT NOT NULL REFERENCES reports (report_number),
        position INTEGER NOT NULL CHECK (position > 0),
        recording_id TEXT NOT NULL,
        sequence TEXT NOT NULL,
        action_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL REFERENCES receipts (receipt_id),
        PRIMARY KEY (report_number, position),
        UNIQUE (report_number, recording_id, sequence),
        FOREIGN KEY (recording_id, sequence) REFERENCES evidence (recording_id, sequence)
    )''',
)


@dataclass(frozen=True)
class ReportEvidenceRef:
    recording_id: str
    sequence: int
    action_id: str
    receipt_id: str


@dataclass(frozen=True)
class SignedReport:
    identity: ReportIdentity
    shift: ShiftSnapshot
    totals: ShiftTotals
    decision: LimitDecision
    references: tuple[ReportEvidenceRef, ...]


class ReportArchive:
    def __init__(self, conn):
        self.conn = conn

    def find_by_shift(self, shift_id):
        row = self.conn.execute('SELECT * FROM reports WHERE shift_id = ?', (shift_id,)).fetchone()
        return self._load(row)

    def find_by_number(self, report_number):
        row = self.conn.execute(
            'SELECT * FROM reports WHERE report_number = ?', (report_number,)
        ).fetchone()
        return self._load(row)

    def _load(self, row):
        if row is None:
            return None
        identity = ReportIdentity(row['report_number'], row['shift_id'], row['recording_id'])
        shift = ShiftSnapshot(
            row['shift_id'], row['device_id'], row['recording_id'], row['revision'],
            decode_quantity(row['microliters_per_pulse']), decode_quantity(row['limit_microliters']),
        )
        totals = ShiftTotals(decode_quantity(row['pulses']), decode_quantity(row['microliters']))
        if row['over_limit'] not in (0, 1):
            raise ValueError('invalid stored limit flag')
        decision = LimitDecision(shift.limit_microliters, bool(row['over_limit']))
        rows = self.conn.execute(
            'SELECT * FROM report_refs WHERE report_number = ? ORDER BY position', (identity.number,)
        )
        references = []
        for expected_position, ref in enumerate(rows, start=1):
            if ref['position'] != expected_position:
                raise ValueError('report reference positions are not contiguous')
            references.append(ReportEvidenceRef(ref['recording_id'], decode_quantity(ref['sequence']),
                                                ref['action_id'], ref['receipt_id']))
        return SignedReport(identity, shift, totals, decision, tuple(references))

    def insert(self, report: SignedReport):
        identity, shift = report.identity, report.shift
        self.conn.execute(
            '''INSERT INTO reports (report_number, shift_id, recording_id, device_id, revision,
                                    microliters_per_pulse, limit_microliters, pulses, microliters, over_limit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (identity.number, identity.shift_id, identity.recording_id, shift.device_id, shift.revision,
             encode_quantity(shift.microliters_per_pulse), encode_quantity(shift.limit_microliters),
             encode_quantity(report.totals.pulses), encode_quantity(report.totals.microliters),
             int(report.decision.over_limit)),
        )
        self.conn.executemany(
            '''INSERT INTO report_refs (report_number, position, recording_id, sequence, action_id, receipt_id)
               VALUES (?, ?, ?, ?, ?, ?)''',
            ((identity.number, position, ref.recording_id, encode_quantity(ref.sequence), ref.action_id,
              ref.receipt_id) for position, ref in enumerate(report.references, start=1)),
        )
