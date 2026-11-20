from threading import Lock, RLock

from station.meter.actions import ActionLedger
from station.meter.register import PulseRegister


class Recording:
    def __init__(self, shift_id, device_id, recording_id, register):
        self.shift_id = shift_id
        self.device_id = device_id
        self.recording_id = recording_id
        self.register = register
        self.actions = ActionLedger(shift_id, device_id, recording_id)
        self.action_lock = Lock()
        self._ingress = Lock()
        self._active = False

    def is_active(self):
        with self._ingress:
            return self._active

    def enable(self):
        with self._ingress:
            self._active = True

    def add(self, pulses):
        with self._ingress:
            if not self._active:
                raise ValueError('recording is stopped')
            self.register.add(pulses)

    def replay(self, action_id, kind):
        original = self.actions.lookup(action_id)
        if original is not None and original.kind != kind:
            raise ValueError('action kind conflict')
        return original

    def capture(self, action_id, kind):
        pulses = self.register.read_clear()
        return self.actions.append(action_id, kind, pulses)

    def finish(self, action_id):
        with self._ingress:
            if not self._active:
                raise ValueError('recording already stopped')
            evidence = self.capture(action_id, 'stop')
            self._active = False
            return evidence


class SessionDirectory:
    def __init__(self, register_factory=PulseRegister):
        self.opening = RLock()
        self._register_factory = register_factory
        self._prepared = {}
        self._recordings = {}
        self._latest_by_device = {}

    def prepare(self, shift_id, device_id, recording_id) -> Recording:
        with self.opening:
            if recording_id in self._recordings or recording_id in self._prepared:
                raise ValueError('recording identity conflict')
            current = self._latest_by_device.get(device_id)
            if current is not None and current.is_active():
                raise ValueError('device already has an active shift')
            if any(item.device_id == device_id for item in self._prepared.values()):
                raise ValueError('device already has a prepared shift')
            recording = Recording(shift_id, device_id, recording_id, self._register_factory())
            self._prepared[recording_id] = recording
            return recording

    def activate(self, recording_id) -> Recording:
        with self.opening:
            recording = self._prepared.pop(recording_id)
            recording.enable()
            self._recordings[recording_id] = recording
            self._latest_by_device[recording.device_id] = recording
            return recording

    def discard(self, recording_id) -> None:
        with self.opening:
            self._prepared.pop(recording_id, None)

    def require(self, recording_id) -> Recording:
        if not isinstance(recording_id, str) or not recording_id.strip():
            raise ValueError('recording_id must be a nonempty identifier')
        with self.opening:
            recording = self._recordings.get(recording_id)
            if recording is None:
                raise LookupError(f'unknown recording {recording_id!r}')
            return recording


def count(sessions: SessionDirectory, recording_id: str, pulses: int) -> None:
    sessions.require(recording_id).add(pulses)
