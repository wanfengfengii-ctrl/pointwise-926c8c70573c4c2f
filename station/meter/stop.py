from station.meter.actions import SourceEvidence
from station.meter.session import SessionDirectory


def stop(sessions: SessionDirectory, recording_id: str, action_id: str) -> SourceEvidence:
    recording = sessions.require(recording_id)
    with recording.action_lock:
        original = recording.replay(action_id, 'stop')
        if original is not None:
            return original
        return recording.finish(action_id)
