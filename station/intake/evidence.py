from dataclasses import dataclass

from station.meter.actions import SourceEvidence, validate_action_id
from station.setup.shifts import ShiftSnapshot


@dataclass(frozen=True)
class AcceptedEvidence:
    shift_id: str
    device_id: str
    recording_id: str
    sequence: int
    action_id: str
    kind: str
    pulses: int
    terminal_sequence: int | None


def validate_source_evidence(source: SourceEvidence, shift: ShiftSnapshot) -> AcceptedEvidence:
    if not isinstance(source, SourceEvidence):
        raise ValueError('expected a source evidence record')
    binding = (source.shift_id, source.device_id, source.recording_id)
    if binding != (shift.shift_id, shift.device_id, shift.recording_id):
        raise ValueError('evidence does not match the frozen shift binding')
    validate_action_id(source.action_id)
    if type(source.sequence) is not int or source.sequence < 1:
        raise ValueError('sequence must be a positive integer')
    if type(source.pulses) is not int or source.pulses < 0:
        raise ValueError('pulses must be a nonnegative integer')
    if source.kind == 'inspection':
        if source.terminal_sequence is not None:
            raise ValueError('inspection cannot declare a terminal sequence')
    elif source.kind == 'stop':
        if type(source.terminal_sequence) is not int or source.terminal_sequence != source.sequence:
            raise ValueError('stop must declare its own sequence as terminal')
    else:
        raise ValueError('unknown evidence kind')
    return AcceptedEvidence(*binding, source.sequence, source.action_id, source.kind,
                            source.pulses, source.terminal_sequence)


def evidence_key(evidence: AcceptedEvidence) -> tuple[str, int, str]:
    return evidence.recording_id, evidence.sequence, evidence.action_id


def same_evidence(first: AcceptedEvidence, later: AcceptedEvidence) -> bool:
    return first == later
