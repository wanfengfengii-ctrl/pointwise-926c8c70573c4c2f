from dataclasses import dataclass

from station.intake.store import EvidenceStore, StoredEvidence


@dataclass(frozen=True)
class CloseEvidence:
    terminal_sequence: int | None
    missing_sequences: tuple[int, ...] = ()
    entries: tuple[StoredEvidence, ...] = ()


def read_close_evidence(store: EvidenceStore, recording_id: str) -> CloseEvidence:
    terminal = store.terminal(recording_id)
    if terminal is None:
        return CloseEvidence(None)
    last = terminal.evidence.terminal_sequence
    entries = store.list_for_recording(recording_id)
    present = {entry.evidence.sequence for entry in entries}
    missing = tuple(sequence for sequence in range(1, last + 1) if sequence not in present)
    if missing:
        return CloseEvidence(last, missing)
    return CloseEvidence(last, entries=entries)
