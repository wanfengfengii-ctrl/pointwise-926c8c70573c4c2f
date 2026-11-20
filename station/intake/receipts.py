from dataclasses import dataclass
from uuid import uuid4

from station.intake.evidence import AcceptedEvidence, evidence_key


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    recording_id: str
    sequence: int
    action_id: str


def make_receipt(evidence: AcceptedEvidence) -> Receipt:
    return Receipt(uuid4().hex, *evidence_key(evidence))
