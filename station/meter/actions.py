from dataclasses import dataclass


@dataclass(frozen=True)
class SourceEvidence:
    shift_id: str
    device_id: str
    recording_id: str
    sequence: int
    action_id: str
    kind: str
    pulses: int
    terminal_sequence: int | None = None


def validate_action_id(action_id):
    if not isinstance(action_id, str) or not action_id.strip():
        raise ValueError('action_id must be a nonempty identifier')


class ActionLedger:
    def __init__(self, shift_id, device_id, recording_id):
        self._identity = (shift_id, device_id, recording_id)
        self._by_action = {}

    def lookup(self, action_id) -> SourceEvidence | None:
        validate_action_id(action_id)
        return self._by_action.get(action_id)

    def next_sequence(self) -> int:
        return len(self._by_action) + 1

    def append(self, action_id, kind, pulses) -> SourceEvidence:
        if self.lookup(action_id) is not None:
            raise ValueError('action already recorded')
        if kind not in ('inspection', 'stop'):
            raise ValueError('unknown source action kind')
        sequence = self.next_sequence()
        terminal_sequence = sequence if kind == 'stop' else None
        evidence = SourceEvidence(*self._identity, sequence, action_id, kind, pulses, terminal_sequence)
        self._by_action[action_id] = evidence
        return evidence
