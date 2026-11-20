from uuid import uuid4

from station.meter.session import SessionDirectory
from station.setup.calibration import CalibrationCatalog
from station.setup.devices import require_identifier
from station.setup.shifts import ShiftRegistry, freeze_shift
from station.setup.storage import StationDatabase, encode_quantity


class Registration:
    def __init__(self, database: StationDatabase, sessions: SessionDirectory):
        self.database = database
        self.sessions = sessions

    def configure(self, device_id, revision, microliters_per_pulse):
        with self.database.transaction('configure') as conn:
            return CalibrationCatalog(conn).configure(device_id, revision, microliters_per_pulse)

    def begin(self, shift_id, device_id, revision, limit_microliters):
        require_identifier(shift_id, 'shift_id')
        require_identifier(device_id, 'device_id')
        require_identifier(revision, 'revision')
        encode_quantity(limit_microliters)
        requested = (device_id, revision, limit_microliters)
        with self.sessions.opening:
            prepared = None
            try:
                with self.database.transaction('begin') as conn:
                    shifts = ShiftRegistry(conn)
                    original = shifts.find(shift_id)
                    if original is not None:
                        frozen = (original.device_id, original.revision, original.limit_microliters)
                        if frozen != requested:
                            raise ValueError('shift parameters conflict')
                        return original
                    calibration = CalibrationCatalog(conn).require(device_id, revision)
                    shift = freeze_shift(shift_id, uuid4().hex, calibration, limit_microliters)
                    prepared = self.sessions.prepare(shift.shift_id, shift.device_id, shift.recording_id)
                    shifts.insert(shift)
            except BaseException:
                if prepared is not None:
                    self.sessions.discard(prepared.recording_id)
                raise
            self.sessions.activate(prepared.recording_id)
            return shift
