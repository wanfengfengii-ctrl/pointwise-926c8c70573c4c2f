"""Two independent V1 archives for the offline handover example."""

from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from station.audit.readback import readback
from station.meter.actions import SourceEvidence
from station.report.archive import SignedReport
from station.setup.bootstrap import StationContext, open_archive, open_station
from station.setup.shifts import ShiftSnapshot
from tests.support import CommitFault


@dataclass(frozen=True)
class HandoverFixture:
    source: StationContext
    target: StationContext
    shift: ShiftSnapshot
    evidence: tuple[SourceEvidence, ...]
    source_report: SignedReport
    fault: CommitFault


def make_fixture(directory: Path) -> HandoverFixture:
    """Seed two fresh files with one opening snapshot and sign only the source."""
    source_path = directory / 'source.sqlite3'
    target_path = directory / 'target.sqlite3'
    if source_path.exists() or target_path.exists():
        raise ValueError('the fixture needs fresh station files')

    source = open_station(source_path)
    source.configure('bench', 'r1', 2)
    shift = source.begin('day', 'bench', 'r1', 14)
    with closing(sqlite3.connect(source_path)) as origin:
        with closing(sqlite3.connect(target_path)) as copied:
            origin.backup(copied)

    fault = CommitFault()
    target = open_station(target_path, before_commit=fault)
    recording = shift.recording_id
    source.count(recording, 7)
    first = source.inspect(recording, 'inspect-1')
    second = source.inspect(recording, 'inspect-2')
    last = source.stop(recording, 'stop')
    for item in (last, first, second):
        source.accept(item)
    outcome = source.publish(shift.shift_id)
    assert outcome.status == 'signed' and outcome.report is not None
    return HandoverFixture(source, target, shift, (first, second, last), outcome.report, fault)


def main() -> None:
    with TemporaryDirectory() as directory:
        fixture = make_fixture(Path(directory))
        source, target = fixture.source, fixture.target
        shift, evidence = fixture.shift, fixture.evidence
        assert target.begin(shift.shift_id, shift.device_id, shift.revision,
                            shift.limit_microliters) == shift
        initial_state = target.publish(shift.shift_id).status
        assert initial_state == 'awaiting_stop'

        local_receipts = tuple(target.accept(item) for item in evidence)
        result = target.publish(shift.shift_id)
        assert result.status == 'signed' and result.report is not None
        original = readback(open_archive(source.database.path), fixture.source_report.identity.number)
        local = readback(open_archive(target.database.path), result.report.identity.number)
        assert original.report.identity.number != local.report.identity.number
        assert original.report.totals == local.report.totals
        assert original.report.decision == local.report.decision
        assert tuple(item.evidence.pulses for item in original.provenance.entries) == (7, 0, 0)
        assert original.report.totals.microliters == 14 and not original.report.decision.over_limit
        for origin_entry, local_entry, receipt in zip(original.provenance.entries,
                                                       local.provenance.entries, local_receipts):
            assert origin_entry.evidence == local_entry.evidence
            assert origin_entry.receipt != receipt
            assert receipt == local_entry.receipt
        assert target.accept(evidence[0]) == local_receipts[0]
        print(json.dumps({'target_before_delivery': initial_state,
                          'shared_pulses': local.report.totals.pulses,
                          'microliters': local.report.totals.microliters,
                          'over_limit': local.report.decision.over_limit,
                          'source_receipts': [item.receipt.receipt_id for item in original.provenance.entries],
                          'local_receipts': [item.receipt_id for item in local_receipts]}))


if __name__ == '__main__':
    main()
