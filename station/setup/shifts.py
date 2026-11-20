from dataclasses import dataclass

from station.setup.calibration import Calibration
from station.setup.devices import require_identifier
from station.setup.storage import decode_quantity, encode_quantity


SHIFT_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS shifts (
        shift_id TEXT PRIMARY KEY NOT NULL,
        device_id TEXT NOT NULL,
        recording_id TEXT NOT NULL UNIQUE,
        revision TEXT NOT NULL,
        microliters_per_pulse TEXT NOT NULL,
        limit_microliters TEXT NOT NULL,
        FOREIGN KEY (device_id, revision) REFERENCES calibrations (device_id, revision)
    )''',
)


@dataclass(frozen=True)
class ShiftSnapshot:
    shift_id: str
    device_id: str
    recording_id: str
    revision: str
    microliters_per_pulse: int
    limit_microliters: int


def freeze_shift(shift_id: str, recording_id: str, calibration: Calibration,
                 limit_microliters: int) -> ShiftSnapshot:
    require_identifier(shift_id, 'shift_id')
    require_identifier(recording_id, 'recording_id')
    encode_quantity(limit_microliters)
    return ShiftSnapshot(shift_id, calibration.device_id, recording_id,
                         calibration.revision, calibration.microliters_per_pulse,
                         limit_microliters)


class ShiftRegistry:
    def __init__(self, conn):
        self.conn = conn

    def find(self, shift_id):
        require_identifier(shift_id, 'shift_id')
        row = self.conn.execute('SELECT * FROM shifts WHERE shift_id = ?', (shift_id,)).fetchone()
        if row is None:
            return None
        return ShiftSnapshot(
            row['shift_id'], row['device_id'], row['recording_id'], row['revision'],
            decode_quantity(row['microliters_per_pulse']), decode_quantity(row['limit_microliters']),
        )

    def insert(self, shift):
        self.conn.execute(
            '''INSERT INTO shifts (shift_id, device_id, recording_id, revision,
                                   microliters_per_pulse, limit_microliters)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (shift.shift_id, shift.device_id, shift.recording_id, shift.revision,
             encode_quantity(shift.microliters_per_pulse), encode_quantity(shift.limit_microliters)),
        )
