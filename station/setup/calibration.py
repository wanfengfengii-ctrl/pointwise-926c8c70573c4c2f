from dataclasses import dataclass

from station.setup.devices import DeviceRegistry, require_identifier
from station.setup.storage import decode_quantity, encode_quantity


CALIBRATION_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS calibrations (
        device_id TEXT NOT NULL REFERENCES devices (device_id),
        revision TEXT NOT NULL,
        microliters_per_pulse TEXT NOT NULL,
        PRIMARY KEY (device_id, revision)
    )''',
)


@dataclass(frozen=True)
class Calibration:
    device_id: str
    revision: str
    microliters_per_pulse: int


class CalibrationCatalog:
    def __init__(self, conn):
        self.conn = conn
        self.devices = DeviceRegistry(conn)

    def _find(self, device_id, revision):
        row = self.conn.execute(
            '''SELECT microliters_per_pulse FROM calibrations
               WHERE device_id = ? AND revision = ?''',
            (device_id, revision),
        ).fetchone()
        if row is None:
            return None
        return Calibration(device_id, revision, decode_quantity(row['microliters_per_pulse']))

    def configure(self, device_id, revision, microliters_per_pulse):
        require_identifier(revision, 'revision')
        encoded = encode_quantity(microliters_per_pulse)
        if microliters_per_pulse == 0:
            raise ValueError('microliters_per_pulse must be positive')
        device = self.devices.register(device_id)
        original = self._find(device.device_id, revision)
        if original is not None:
            if original.microliters_per_pulse != microliters_per_pulse:
                raise ValueError('calibration revision conflict')
            return original
        self.conn.execute(
            '''INSERT INTO calibrations (device_id, revision, microliters_per_pulse)
               VALUES (?, ?, ?)''',
            (device.device_id, revision, encoded),
        )
        return Calibration(device.device_id, revision, microliters_per_pulse)

    def require(self, device_id, revision):
        device = self.devices.require(device_id)
        require_identifier(revision, 'revision')
        calibration = self._find(device.device_id, revision)
        if calibration is None:
            raise LookupError(f'unknown calibration {device_id!r}/{revision!r}')
        return calibration
