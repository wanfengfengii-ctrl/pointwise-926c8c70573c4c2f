from station.audit.recompute import RecomputedReport
from station.report.archive import SignedReport
from station.setup.shifts import ShiftSnapshot


class VerificationError(ValueError):
    pass


def verify_report(report: SignedReport, report_number: str, shift: ShiftSnapshot,
                  recomputed: RecomputedReport) -> None:
    if (report.identity.number, report.identity.shift_id, report.identity.recording_id) != (
        report_number, shift.shift_id, shift.recording_id
    ):
        raise VerificationError('report identity does not match the frozen shift')
    if report.shift != shift:
        raise VerificationError('report calibration or frozen shift terms mismatch')
    if report.totals.pulses != recomputed.totals.pulses:
        raise VerificationError('report pulse total mismatch')
    if report.totals.microliters != recomputed.totals.microliters:
        raise VerificationError('report microliter total mismatch')
    if report.decision != recomputed.decision:
        raise VerificationError('report limit decision mismatch')
