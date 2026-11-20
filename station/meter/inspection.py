from station.meter.actions import SourceEvidence
from station.meter.session import SessionDirectory


def inspect(sessions: SessionDirectory, recording_id: str, action_id: str) -> SourceEvidence:
    recording = sessions.require(recording_id)
    with recording.action_lock:
        original = recording.replay(action_id, 'inspection')
        if original is not None:
            return original
        if not recording.is_active():
            raise ValueError('recording is stopped')
        return recording.capture(action_id, 'inspection')
