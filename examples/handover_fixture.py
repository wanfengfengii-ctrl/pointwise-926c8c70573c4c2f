"""Two independent V1 archives for the offline handover example."""

from contextlib import closing
from dataclasses import dataclass, replace
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from station.audit.readback import readback
from station.meter.actions import SourceEvidence
from station.report.archive import SignedReport
from station.report.handover import export_handover, import_handover
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


def _receipt_ids(result):
    return [entry.receipt.receipt_id for entry in result.provenance.entries]


def _fresh_fixture(root: Path, name: str) -> HandoverFixture:
    target_dir = root / name
    target_dir.mkdir()
    return make_fixture(target_dir)


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        # ------------------------------------------------------------------ #
        # V1 baseline: two stations independently issue their own receipts.
        # ------------------------------------------------------------------ #
        fixture = _fresh_fixture(root, 'baseline')
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

        # ------------------------------------------------------------------ #
        # Order A: import first, then local intake against imported originals.
        # ------------------------------------------------------------------ #
        imported_first = _fresh_fixture(root, 'import-first')
        source, target = imported_first.source, imported_first.target
        shift, evidence = imported_first.shift, imported_first.evidence
        target.begin(shift.shift_id, shift.device_id, shift.revision, shift.limit_microliters)
        package = export_handover(open_archive(source.database.path),
                                  imported_first.source_report.identity.number)
        foreign_number = import_handover(target.database, json.loads(json.dumps(package)))
        assert foreign_number == imported_first.source_report.identity.number
        # Equivalent redelivery of the same number and package.
        assert import_handover(target.database, json.loads(json.dumps(package))) == foreign_number
        assert target.publish(shift.shift_id).status == 'awaiting_stop'

        # The imported original constrains local intake: 8 pulses are rejected,
        # the original 7 is accepted and redelivers the same local receipt.
        try:
            target.accept(replace(evidence[0], pulses=8))
        except ValueError as exc:
            assert 'imported originals' in str(exc)
        else:
            raise AssertionError('modified original must be rejected after import')
        target_first_receipt = target.accept(evidence[0])
        assert target.accept(evidence[0]) == target_first_receipt
        target.accept(evidence[1])
        target.accept(evidence[2])
        local_outcome = target.publish(shift.shift_id)
        assert local_outcome.status == 'signed'
        local_number = local_outcome.report.identity.number
        assert local_number != foreign_number

        # ------------------------------------------------------------------ #
        # Failure continuation: an armed commit fault aborts the whole package.
        # ------------------------------------------------------------------ #
        faulty = _fresh_fixture(root, 'faulty')
        faulty.target.begin(shift.shift_id, shift.device_id, shift.revision, shift.limit_microliters)
        faulty_package = export_handover(open_archive(faulty.source.database.path),
                                         faulty.source_report.identity.number)
        faulty.fault.arm('import_handover')
        try:
            import_handover(faulty.target.database, json.loads(json.dumps(faulty_package)))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError('armed commit fault must propagate')
        with faulty.target.database.read_snapshot() as conn:
            assert conn.execute('SELECT count(*) FROM handover_reports').fetchone()[0] == 0
        assert faulty.target.publish(shift.shift_id).status == 'awaiting_stop'
        recovered_number = import_handover(faulty.target.database,
                                           json.loads(json.dumps(faulty_package)))
        assert recovered_number == faulty.source_report.identity.number

        # ------------------------------------------------------------------ #
        # Order B: local intake first; a conflicting local value blocks import.
        # ------------------------------------------------------------------ #
        local_first = _fresh_fixture(root, 'local-first')
        source, target = local_first.source, local_first.target
        shift, evidence = local_first.shift, local_first.evidence
        target.begin(shift.shift_id, shift.device_id, shift.revision, shift.limit_microliters)
        local_eight = target.accept(replace(evidence[0], pulses=8))
        conflicting_package = export_handover(open_archive(source.database.path),
                                              local_first.source_report.identity.number)
        try:
            import_handover(target.database, json.loads(json.dumps(conflicting_package)))
        except ValueError as exc:
            assert 'conflicts with the handover original' in str(exc)
        else:
            raise AssertionError('import must reject a conflicting local original')
        assert target.accept(replace(evidence[0], pulses=8)) == local_eight

        # A fresh target that accepted the same originals accepts the package.
        matching = _fresh_fixture(root, 'local-match')
        source, target = matching.source, matching.target
        shift, evidence = matching.shift, matching.evidence
        target.begin(shift.shift_id, shift.device_id, shift.revision, shift.limit_microliters)
        target.accept(evidence[2])
        assert target.publish(shift.shift_id).missing_sequences == (1, 2)
        matching_package = export_handover(open_archive(source.database.path),
                                           matching.source_report.identity.number)
        matching_foreign = import_handover(target.database,
                                           json.loads(json.dumps(matching_package)))
        target.accept(evidence[0])
        target.accept(evidence[1])
        matching_local = target.publish(shift.shift_id)
        assert matching_local.status == 'signed'

        # ------------------------------------------------------------------ #
        # Source station stopped: reopen the target archive and claim both
        # reports by their own numbers, each with its adopted first receipts.
        # ------------------------------------------------------------------ #
        target_path = imported_first.target.database.path
        source_receipt_ids = _receipt_ids(
            readback(open_archive(imported_first.source.database.path), foreign_number)
        )
        del imported_first, source, target
        reopened_archive = open_archive(target_path)
        foreign_readback = readback(reopened_archive, foreign_number)
        local_readback = readback(reopened_archive, local_number)
        assert _receipt_ids(foreign_readback) == source_receipt_ids
        assert _receipt_ids(local_readback) != source_receipt_ids
        assert foreign_readback.report.totals == local_readback.report.totals
        print(json.dumps({
            'scenario': 'source_stopped_dual_report_readback',
            'foreign_report': foreign_number,
            'local_report': local_number,
            'foreign_receipts': _receipt_ids(foreign_readback),
            'local_receipts': _receipt_ids(local_readback),
            'pulses': foreign_readback.report.totals.pulses,
            'microliters': foreign_readback.report.totals.microliters,
            'over_limit': foreign_readback.report.decision.over_limit,
            'fault_recovered_report': recovered_number,
            'local_first_conflict_rejected': True,
            'matching_local_report': matching_local.report.identity.number,
        }, sort_keys=True))


if __name__ == '__main__':
    main()
