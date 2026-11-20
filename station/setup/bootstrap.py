from dataclasses import dataclass

from station.audit.readback import readback
from station.intake.receive import accept
from station.intake.store import INTAKE_SCHEMA
from station.meter.inspection import inspect
from station.meter.session import SessionDirectory, count
from station.meter.stop import stop
from station.report.archive import REPORT_SCHEMA
from station.report.handover import HANDOVER_SCHEMA
from station.report.publish import publish
from station.setup.calibration import CALIBRATION_SCHEMA
from station.setup.devices import DEVICE_SCHEMA
from station.setup.registration import Registration
from station.setup.shifts import SHIFT_SCHEMA
from station.setup.storage import StationDatabase


@dataclass(frozen=True)
class StationContext:
    database: StationDatabase
    registration: Registration
    sessions: SessionDirectory

    def configure(self, device_id, revision, microliters_per_pulse):
        return self.registration.configure(device_id, revision, microliters_per_pulse)

    def begin(self, shift_id, device_id, revision, limit_microliters):
        return self.registration.begin(shift_id, device_id, revision, limit_microliters)

    def count(self, recording_id, pulses):
        return count(self.sessions, recording_id, pulses)

    def inspect(self, recording_id, action_id):
        return inspect(self.sessions, recording_id, action_id)

    def stop(self, recording_id, action_id):
        return stop(self.sessions, recording_id, action_id)

    def accept(self, evidence):
        return accept(self.database, evidence)

    def publish(self, shift_id):
        return publish(self.database, shift_id)

    def readback(self, report_number):
        return readback(self.database, report_number)


def open_station(path, *, sessions=None, before_commit=None) -> StationContext:
    database = StationDatabase(path, before_commit=before_commit)
    database.initialize(DEVICE_SCHEMA, CALIBRATION_SCHEMA, SHIFT_SCHEMA, INTAKE_SCHEMA,
                        REPORT_SCHEMA, HANDOVER_SCHEMA)
    if sessions is None:
        sessions = SessionDirectory()
    return StationContext(database, Registration(database, sessions), sessions)


def open_archive(path) -> StationDatabase:
    return StationDatabase(path, read_only=True)
