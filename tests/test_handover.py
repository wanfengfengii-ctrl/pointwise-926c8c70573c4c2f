"""Regression tests for the offline signed-report handover."""

import copy
import json
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from dataclasses import replace
import unittest

from examples.handover_fixture import make_fixture
from station.audit.readback import readback
from station.audit.verification import VerificationError
from station.report.handover import (
    HandoverStore,
    decode_handover,
    export_handover,
    import_handover,
)
from station.setup.bootstrap import open_archive, open_station


DOCUMENTED_PACKAGE = {
    "report": {
        "identity": {"number": "R-example", "shift_id": "day", "recording_id": "rec-example"},
        "shift": {"shift_id": "day", "device_id": "bench", "recording_id": "rec-example",
                  "revision": "r1", "microliters_per_pulse": "2", "limit_microliters": "e"},
        "totals": {"pulses": "0", "microliters": "0"},
        "decision": {"limit_microliters": "e", "over_limit": False},
        "references": [{"recording_id": "rec-example", "sequence": "1", "action_id": "stop",
                        "receipt_id": "origin-1"}],
    },
    "entries": [{
        "evidence": {"shift_id": "day", "device_id": "bench", "recording_id": "rec-example",
                     "sequence": "1", "action_id": "stop", "kind": "stop", "pulses": "0",
                     "terminal_sequence": "1"},
        "receipt": {"receipt_id": "origin-1", "recording_id": "rec-example",
                    "sequence": "1", "action_id": "stop"},
    }],
}


class HandoverCase:
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fixture = make_fixture(Path(self.directory.name))
        self.source = self.fixture.source
        self.target = self.fixture.target
        self.shift = self.fixture.shift
        self.evidence = self.fixture.evidence
        self.target.begin(self.shift.shift_id, self.shift.device_id,
                          self.shift.revision, self.shift.limit_microliters)
        self.number = self.fixture.source_report.identity.number
        self.package = export_handover(open_archive(self.source.database.path), self.number)

    def _import(self):
        return import_handover(self.target.database, json.loads(json.dumps(self.package)))

    def _local_receipts(self):
        return [self.target.accept(item) for item in self.evidence]


class TestExportImport(HandoverCase, unittest.TestCase):
    def test_export_matches_documented_shape(self):
        report = self.package['report']
        self.assertEqual(set(self.package), {'report', 'entries'})
        self.assertEqual(set(report), {'identity', 'shift', 'totals', 'decision', 'references'})
        self.assertEqual(report['identity'],
                         {'number': self.number, 'shift_id': 'day',
                          'recording_id': self.shift.recording_id})
        self.assertEqual(report['shift'], {
            'shift_id': 'day', 'device_id': 'bench', 'recording_id': self.shift.recording_id,
            'revision': 'r1', 'microliters_per_pulse': '2', 'limit_microliters': 'e',
        })
        self.assertEqual(report['totals'], {'pulses': '7', 'microliters': 'e'})
        self.assertEqual(report['decision'], {'limit_microliters': 'e', 'over_limit': False})
        self.assertEqual([entry['evidence']['pulses'] for entry in self.package['entries']],
                         ['7', '0', '0'])
        self.assertEqual([entry['evidence']['terminal_sequence'] for entry in self.package['entries']],
                         [None, None, '3'])
        self.assertEqual([ref['sequence'] for ref in report['references']], ['1', '2', '3'])
        # JSON round trip is lossless and key reordering is tolerated.
        self.assertEqual(json.loads(json.dumps(self.package)), self.package)
        decoded = decode_handover(json.loads(json.dumps(self.package)))
        self.assertEqual(decoded.report, self.fixture.source_report)

    def test_unknown_report_keeps_verification_error(self):
        with self.assertRaisesRegex(VerificationError, 'unknown report'):
            export_handover(open_archive(self.source.database.path), 'R-missing')

    def test_import_returns_original_number_and_readback_keeps_source_receipts(self):
        self.assertEqual(self._import(), self.number)
        imported = readback(open_archive(self.target.database.path), self.number)
        original = readback(open_archive(self.source.database.path), self.number)
        self.assertEqual(imported.report, original.report)
        self.assertEqual(
            [entry.receipt for entry in imported.provenance.entries],
            [entry.receipt for entry in original.provenance.entries],
        )
        self.assertEqual(
            [entry.evidence for entry in imported.provenance.entries],
            [entry.evidence for entry in original.provenance.entries],
        )

    def test_equivalent_redelivery_returns_same_number(self):
        self.assertEqual(self._import(), self.number)
        reordered = {'entries': self.package['entries'], 'report': self.package['report']}
        self.assertEqual(import_handover(self.target.database, reordered), self.number)
        with self.target.database.read_snapshot() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM handover_reports').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM handover_evidence').fetchone()[0], 3)

    def test_imported_originals_do_not_count_as_local_intake(self):
        self._import()
        self.assertEqual(self.target.publish(self.shift.shift_id).status, 'awaiting_stop')
        local_receipts = self._local_receipts()
        outcome = self.target.publish(self.shift.shift_id)
        self.assertEqual(outcome.status, 'signed')
        self.assertNotEqual(outcome.report.identity.number, self.number)
        foreign = readback(open_archive(self.target.database.path), self.number)
        local = readback(open_archive(self.target.database.path), outcome.report.identity.number)
        self.assertEqual(local.report.totals, foreign.report.totals)
        self.assertNotEqual(
            [entry.receipt for entry in local.provenance.entries],
            [entry.receipt for entry in foreign.provenance.entries],
        )
        self.assertEqual([entry.receipt.receipt_id for entry in local.provenance.entries],
                         [receipt.receipt_id for receipt in local_receipts])


class TestImportFirstOrdering(HandoverCase, unittest.TestCase):
    def test_modified_local_original_is_rejected(self):
        self._import()
        modified = replace(self.evidence[0], pulses=8)
        with self.assertRaisesRegex(ValueError, 'imported originals'):
            self.target.accept(modified)
        # The original value is still accepted for the first local receipt.
        first_receipt = self.target.accept(self.evidence[0])
        self.assertEqual(self.target.accept(self.evidence[0]), first_receipt)

    def test_local_action_conflict_is_rejected(self):
        self._import()
        foreign_action = replace(self.evidence[0], action_id='different-action')
        with self.assertRaisesRegex(ValueError, 'imported originals'):
            self.target.accept(foreign_action)

    def test_local_evidence_beyond_terminal_is_rejected(self):
        self._import()
        late = replace(self.evidence[2], sequence=4, terminal_sequence=4, action_id='late-stop')
        with self.assertRaisesRegex(ValueError, 'imported stop boundary'):
            self.target.accept(late)

    def test_local_completion_uses_its_own_receipts(self):
        self._import()
        receipts = self._local_receipts()
        foreign = readback(open_archive(self.target.database.path), self.number)
        for local, imported in zip(receipts, foreign.provenance.entries):
            self.assertNotEqual(local.receipt_id, imported.receipt.receipt_id)
        self.assertEqual(self.target.publish(self.shift.shift_id).status, 'signed')


class TestLocalFirstOrdering(HandoverCase, unittest.TestCase):
    def test_import_rejects_conflicting_local_original(self):
        modified = replace(self.evidence[0], pulses=8)
        local_receipt = self.target.accept(modified)
        with self.assertRaisesRegex(ValueError, 'conflicts with the handover original'):
            self._import()
        # Old ledger is untouched after the rejected package.
        self.assertEqual(self.target.accept(modified), local_receipt)
        with self.target.database.read_snapshot() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM handover_reports').fetchone()[0], 0)
        self.assertEqual(self.target.publish(self.shift.shift_id).status, 'awaiting_stop')

    def test_import_after_local_terminal_then_missing_gaps(self):
        terminal_receipt = self.target.accept(self.evidence[2])
        self.assertEqual(self.target.publish(self.shift.shift_id).missing_sequences, (1, 2))
        self.assertEqual(self._import(), self.number)
        self.target.accept(self.evidence[0])
        self.assertEqual(self.target.publish(self.shift.shift_id).missing_sequences, (2,))
        self.target.accept(self.evidence[1])
        outcome = self.target.publish(self.shift.shift_id)
        self.assertEqual(outcome.status, 'signed')
        self.assertEqual(self.target.accept(self.evidence[2]), terminal_receipt)

    def test_local_terminal_boundary_must_match_package(self):
        self.target.accept(self.evidence[2])
        with sqlite3.connect(self.target.database.path) as conn:
            conn.execute(
                "UPDATE evidence SET sequence = '2', terminal_sequence = '2' "
                "WHERE recording_id = ? AND kind = 'stop'",
                (self.shift.recording_id,),
            )
            conn.execute(
                "UPDATE receipts SET sequence = '2' "
                "WHERE recording_id = ? AND action_id = 'stop'",
                (self.shift.recording_id,),
            )
        with self.assertRaisesRegex(ValueError, 'local stop boundary'):
            self._import()


class TestImportSafety(HandoverCase, unittest.TestCase):
    def test_commit_failure_keeps_the_old_ledger(self):
        self.fixture.fault.arm('import_handover')
        with self.assertRaises(sqlite3.IntegrityError):
            self._import()
        with self.target.database.read_snapshot() as conn:
            for table in ('handover_reports', 'handover_evidence',
                          'handover_receipts', 'handover_refs'):
                self.assertEqual(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        # Following deliveries are still adjudicated from the clean state.
        self.assertEqual(self._import(), self.number)
        self._local_receipts()
        self.assertEqual(self.target.publish(self.shift.shift_id).status, 'signed')

    def test_report_number_conflict_with_local_report(self):
        self._local_receipts()
        local_number = self.target.publish(self.shift.shift_id).report.identity.number
        reused = copy.deepcopy(self.package)
        reused['report']['identity']['number'] = local_number
        with self.assertRaisesRegex(ValueError, 'report number is already used by a local report'):
            import_handover(self.target.database, reused)

    def test_import_alongside_a_signed_local_report_with_distinct_numbers(self):
        local_receipts = self._local_receipts()
        local_number = self.target.publish(self.shift.shift_id).report.identity.number
        self.assertEqual(self._import(), self.number)
        foreign = readback(open_archive(self.target.database.path), self.number)
        local = readback(open_archive(self.target.database.path), local_number)
        self.assertEqual(
            [entry.receipt.receipt_id for entry in local.provenance.entries],
            [receipt.receipt_id for receipt in local_receipts],
        )
        self.assertNotEqual(
            [entry.receipt.receipt_id for entry in foreign.provenance.entries],
            [receipt.receipt_id for receipt in local_receipts],
        )

    def test_conflicting_redelivery_is_rejected(self):
        self._import()
        changed = copy.deepcopy(self.package)
        changed['report']['identity']['number'] = 'R-other'
        with self.assertRaisesRegex(ValueError, 'already carries another handover report'):
            import_handover(self.target.database, changed)

    def test_frozen_snapshot_mismatch_rejects_package(self):
        with sqlite3.connect(self.target.database.path) as conn:
            conn.execute('UPDATE shifts SET microliters_per_pulse = ? WHERE shift_id = ?',
                         ('3', 'day'))
        with self.assertRaisesRegex(ValueError, 'frozen opening snapshot'):
            self._import()

    def test_unknown_shift_is_lookup_error(self):
        with sqlite3.connect(self.target.database.path) as conn:
            conn.execute('DELETE FROM shifts WHERE shift_id = ?', ('day',))
        with self.assertRaises(LookupError):
            self._import()

    def test_read_only_archive_cannot_import(self):
        archive = open_archive(self.target.database.path)
        with self.assertRaisesRegex(ValueError, 'read-only'):
            import_handover(archive, self.package)


class TestMalformedPackages(HandoverCase, unittest.TestCase):
    def _reject(self, bad):
        with self.assertRaisesRegex(ValueError, 'invalid handover package'):
            import_handover(self.target.database, bad)

    def test_structural_failures(self):
        self._reject([])
        missing = copy.deepcopy(self.package)
        del missing['entries']
        self._reject(missing)
        extra = copy.deepcopy(self.package)
        extra['unexpected'] = True
        self._reject(extra)
        blank = copy.deepcopy(self.package)
        blank['report']['identity']['number'] = '   '
        self._reject(blank)

    def test_measurement_failures(self):
        bad_total = copy.deepcopy(self.package)
        bad_total['report']['totals']['pulses'] = '8'
        self._reject(bad_total)
        bad_code = copy.deepcopy(self.package)
        bad_code['entries'][0]['evidence']['pulses'] = '8'
        self._reject(bad_code)
        bad_decision = copy.deepcopy(self.package)
        bad_decision['report']['decision']['over_limit'] = True
        self._reject(bad_decision)

    def test_identity_failures(self):
        noncanonical = copy.deepcopy(self.package)
        noncanonical['entries'][0]['evidence']['sequence'] = '01'
        self._reject(noncanonical)
        duplicate_action = copy.deepcopy(self.package)
        duplicate_action['entries'][1]['evidence']['action_id'] = \
            duplicate_action['entries'][0]['evidence']['action_id']
        self._reject(duplicate_action)
        duplicate_receipt = copy.deepcopy(self.package)
        duplicate_receipt['entries'][1]['receipt']['receipt_id'] = \
            duplicate_receipt['entries'][0]['receipt']['receipt_id']
        self._reject(duplicate_receipt)
        null_terminal = copy.deepcopy(self.package)
        null_terminal['entries'][2]['evidence']['terminal_sequence'] = None
        self._reject(null_terminal)
        reference_mismatch = copy.deepcopy(self.package)
        reference_mismatch['report']['references'][0]['sequence'] = '2'
        self._reject(reference_mismatch)

    def test_nothing_persists_after_rejection(self):
        bad = copy.deepcopy(self.package)
        bad['report']['totals']['pulses'] = '8'
        with self.assertRaises(ValueError):
            import_handover(self.target.database, bad)
        with self.target.database.read_snapshot() as conn:
            for table in ('handover_reports', 'handover_evidence',
                          'handover_receipts', 'handover_refs'):
                self.assertEqual(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)


class TestReopenAndDocumentedPackage(unittest.TestCase):
    def test_close_source_and_claim_both_reports_by_number(self):
        with TemporaryDirectory() as directory:
            fixture = make_fixture(Path(directory))
            source, target = fixture.source, fixture.target
            shift, evidence = fixture.shift, fixture.evidence
            target.begin(shift.shift_id, shift.device_id, shift.revision, shift.limit_microliters)
            package = export_handover(open_archive(source.database.path),
                                      fixture.source_report.identity.number)
            foreign_number = import_handover(target.database, json.loads(json.dumps(package)))
            local_receipts = [target.accept(item) for item in evidence]
            local_number = target.publish(shift.shift_id).report.identity.number
            source_receipts = [
                entry.receipt.receipt_id
                for entry in readback(open_archive(source.database.path), foreign_number).provenance.entries
            ]
            del source, target

            archive = open_archive(Path(directory) / 'target.sqlite3')
            foreign = readback(archive, foreign_number)
            local = readback(archive, local_number)
            self.assertEqual(
                [entry.receipt.receipt_id for entry in foreign.provenance.entries], source_receipts
            )
            self.assertEqual(
                [entry.receipt.receipt_id for entry in local.provenance.entries],
                [receipt.receipt_id for receipt in local_receipts],
            )
            self.assertEqual(foreign.report.totals, local.report.totals)
            self.assertNotEqual(foreign.report.identity.number, local.report.identity.number)

    def test_documented_zero_terminal_package(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'station.sqlite3'
            station = open_station(path)
            station.configure('bench', 'r1', 2)
            station.begin('day', 'bench', 'r1', 14)
            with sqlite3.connect(path) as conn:
                conn.execute('UPDATE shifts SET recording_id = ? WHERE shift_id = ?',
                             ('rec-example', 'day'))
            self.assertEqual(
                import_handover(station.database, copy.deepcopy(DOCUMENTED_PACKAGE)), 'R-example'
            )
            result = readback(open_archive(path), 'R-example')
            self.assertEqual(result.report.totals.pulses, 0)
            self.assertEqual(result.report.totals.microliters, 0)
            self.assertEqual([entry.receipt.receipt_id for entry in result.provenance.entries],
                             ['origin-1'])
            self.assertEqual(station.publish('day').status, 'awaiting_stop')


class TestExactnessAndBoundaries(unittest.TestCase):
    def test_huge_quantities_survive_the_handover(self):
        huge = (1 << 68) + 3
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / 'source.sqlite3'
            target_path = root / 'target.sqlite3'
            source = open_station(source_path)
            source.configure('bench', 'r1', 2)
            shift = source.begin('day', 'bench', 'r1', (1 << 69) + 6)
            # Copy the frozen opening snapshot before any intake or signing.
            with closing(sqlite3.connect(source_path)) as origin:
                with closing(sqlite3.connect(target_path)) as copied:
                    origin.backup(copied)
            source.count(shift.recording_id, huge)
            source.accept(source.stop(shift.recording_id, 'stop'))
            signed = source.publish('day').report
            target = open_station(target_path)
            target.begin('day', 'bench', 'r1', (1 << 69) + 6)
            package = export_handover(open_archive(source_path), signed.identity.number)
            import_handover(target.database, json.loads(json.dumps(package)))
            result = readback(open_archive(target_path), signed.identity.number)
            self.assertEqual(result.report.totals.pulses, huge)
            self.assertEqual(result.report.totals.microliters, huge * 2)
            self.assertFalse(result.report.decision.over_limit)

    def test_local_evidence_beyond_package_terminal_blocks_import(self):
        with TemporaryDirectory() as directory:
            fixture = make_fixture(Path(directory))
            target = fixture.target
            shift, evidence = fixture.shift, fixture.evidence
            target.begin(shift.shift_id, shift.device_id, shift.revision, shift.limit_microliters)
            # No local stop yet, so an out-of-range inspection can be planted.
            target.accept(replace(evidence[0], sequence=4, action_id='late-inspection'))
            package = export_handover(open_archive(fixture.source.database.path),
                                      fixture.source_report.identity.number)
            with self.assertRaisesRegex(ValueError, 'beyond the handover stop boundary'):
                import_handover(target.database, json.loads(json.dumps(package)))

    def test_distinct_local_stop_boundary_blocks_import(self):
        with TemporaryDirectory() as directory:
            fixture = make_fixture(Path(directory))
            target = fixture.target
            shift, evidence = fixture.shift, fixture.evidence
            target.begin(shift.shift_id, shift.device_id, shift.revision, shift.limit_microliters)
            target.accept(evidence[2])
            # Move the local stop boundary one position earlier inside the
            # local store; the well-formed package still ends at position 3.
            with sqlite3.connect(target.database.path) as conn:
                conn.execute(
                    "UPDATE evidence SET sequence = '2', terminal_sequence = '2' "
                    "WHERE recording_id = ? AND kind = 'stop'",
                    (shift.recording_id,),
                )
                conn.execute(
                    "UPDATE receipts SET sequence = '2' "
                    "WHERE recording_id = ? AND action_id = 'stop'",
                    (shift.recording_id,),
                )
            package = export_handover(open_archive(fixture.source.database.path),
                                      fixture.source_report.identity.number)
            with self.assertRaisesRegex(ValueError, 'local stop boundary'):
                import_handover(target.database, json.loads(json.dumps(package)))


class TestRuntimeNarrative(unittest.TestCase):
    """Both arrival orders, failure continuation and source-off dual claims."""

    def _package(self, fixture):
        return export_handover(open_archive(fixture.source.database.path),
                               fixture.source_report.identity.number)

    def test_both_arrival_orders_fault_continuation_and_dual_readback(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)

            # Arrival order A: import first, local intake afterwards.
            order_a_dir = root / 'order-a'
            order_a_dir.mkdir()
            order_a = make_fixture(order_a_dir)
            target = order_a.target
            shift, evidence = order_a.shift, order_a.evidence
            target.begin(shift.shift_id, shift.device_id, shift.revision, shift.limit_microliters)
            package = self._package(order_a)
            foreign_number = import_handover(target.database, json.loads(json.dumps(package)))
            with self.assertRaises(ValueError):
                target.accept(replace(evidence[0], pulses=8))
            local_receipts = [target.accept(item) for item in evidence]
            self.assertEqual(target.publish(shift.shift_id).status, 'signed')

            # Arrival order B: local intake first, then the package.
            order_b_dir = root / 'order-b'
            order_b_dir.mkdir()
            order_b = make_fixture(order_b_dir)
            target = order_b.target
            shift, evidence = order_b.shift, order_b.evidence
            target.begin(shift.shift_id, shift.device_id, shift.revision, shift.limit_microliters)
            target.accept(evidence[2])
            self.assertEqual(target.publish(shift.shift_id).missing_sequences, (1, 2))
            import_handover(target.database, json.loads(json.dumps(self._package(order_b))))
            target.accept(evidence[0])
            target.accept(evidence[1])
            self.assertEqual(target.publish(shift.shift_id).status, 'signed')

            # Failure continuation: an armed commit fault aborts the import,
            # then the same package is accepted on retry.
            fault_dir = root / 'fault'
            fault_dir.mkdir()
            faulty = make_fixture(fault_dir)
            faulty.target.begin(shift.shift_id, shift.device_id, shift.revision,
                                shift.limit_microliters)
            faulty_package = self._package(faulty)
            faulty.fault.arm('import_handover')
            with self.assertRaises(sqlite3.IntegrityError):
                import_handover(faulty.target.database, json.loads(json.dumps(faulty_package)))
            with faulty.target.database.read_snapshot() as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM handover_reports').fetchone()[0], 0)
            self.assertEqual(
                import_handover(faulty.target.database, json.loads(json.dumps(faulty_package))),
                faulty.source_report.identity.number,
            )

            # Source stopped: reopen the target archive and claim both reports
            # by their original numbers with their own adopted first receipts.
            target_path = order_a.target.database.path
            source_receipts = [
                entry.receipt.receipt_id
                for entry in readback(open_archive(order_a.source.database.path),
                                      foreign_number).provenance.entries
            ]
            local_number = order_a.target.publish(shift.shift_id).report.identity.number
            del order_a, order_b, faulty, target
            archive = open_archive(target_path)
            foreign = readback(archive, foreign_number)
            local = readback(archive, local_number)
            self.assertEqual([entry.receipt.receipt_id for entry in foreign.provenance.entries],
                             source_receipts)
            self.assertEqual([entry.receipt.receipt_id for entry in local.provenance.entries],
                             [receipt.receipt_id for receipt in local_receipts])
            self.assertNotEqual(
                [entry.receipt.receipt_id for entry in foreign.provenance.entries],
                [entry.receipt.receipt_id for entry in local.provenance.entries],
            )
