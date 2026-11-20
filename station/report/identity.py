from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class ReportIdentity:
    number: str
    shift_id: str
    recording_id: str


def new_report_identity(shift_id: str, recording_id: str) -> ReportIdentity:
    return ReportIdentity(f'R-{uuid4().hex}', shift_id, recording_id)
