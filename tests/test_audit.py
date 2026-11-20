from contextlib import closing
import sqlite3

from station.audit.provenance import ProvenanceError
from station.audit.readback import readback
from station.audit.verification import VerificationError
from station.intake.store import EvidenceStore
from station.setup.bootstrap import open_archive
from tests.support import StationCase


class TestAudit(StationCase):
    def _signed(self, shift_id):
        shift = self.begin(shift_id)
        self.station.count(shift.recording_id, 7)
        first = self.station.inspect(shift.recording_id, 'first')
        terminal = self.station.stop(shift.recording_id, 'stop')
        first_receipt, terminal_receipt = self.deliver(first, terminal)
        report = self.station.publish(shift_id).report
        return report, (first_receipt, terminal_receipt)

    def test_reopens_without_meter(self):
        report, receipts = self._signed('day')
        self.station.configure('bench', 'r2', 3)
        self.station = None
        self.sessions = None
        archive = open_archive(self.path)
        result = readback(archive, report.identity.number)
        self.assertEqual(result.report, report)
        self.assertEqual((result.report.shift.revision, result.report.shift.microliters_per_pulse), ('r1', 2))
        self.assertEqual((result.report.totals.pulses, result.report.totals.microliters), (7, 14))
        self.assertFalse(result.report.decision.over_limit)
        self.assertEqual(tuple(item.receipt for item in result.provenance.entries), receipts)
        self.assertEqual([item.evidence.action_id for item in result.provenance.entries], ['first', 'stop'])
        self.assertEqual(tuple(ref.receipt_id for ref in result.report.references),
                         tuple(receipt.receipt_id for receipt in receipts))
        with self.assertRaisesRegex(VerificationError, 'unknown report'):
            readback(archive, 'unknown')

    def test_rejects_missing_evidence(self):
        target, _ = self._signed('day')
        other, _ = self._signed('other')
        path = self.backup_archive()
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute('DELETE FROM evidence WHERE recording_id = ? AND sequence = ?',
                         (target.identity.recording_id, '1'))
        self.station = None
        self.sessions = None
        archive = open_archive(path)
        with archive.read_snapshot() as conn:
            store = EvidenceStore(conn)
            self.assertIsNone(store.by_sequence(target.identity.recording_id, 1))
            self.assertEqual(store.by_sequence(other.identity.recording_id, 1).evidence.pulses, 7)
        with self.assertRaisesRegex(ProvenanceError, 'missing evidence.*sequence 1'):
            readback(archive, target.identity.number)
        self.assertEqual(readback(archive, other.identity.number).report, other)

    def test_rejects_value_mismatch(self):
        target, receipts = self._signed('day')
        path = self.backup_archive()
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute('UPDATE reports SET microliters = ? WHERE report_number = ?',
                         ('f', target.identity.number))
        archive = open_archive(path)
        with archive.read_snapshot() as conn:
            originals = EvidenceStore(conn).list_for_recording(target.identity.recording_id)
            self.assertEqual([item.evidence.pulses for item in originals], [7, 0])
            self.assertEqual(tuple(item.receipt for item in originals), receipts)
        self.station = None
        self.sessions = None
        with self.assertRaisesRegex(VerificationError, 'microliter total mismatch'):
            readback(archive, target.identity.number)

    def test_rejects_reference_to_another_receipt(self):
        target, receipts = self._signed('day')
        path = self.backup_archive()
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute('UPDATE report_refs SET receipt_id = ? WHERE report_number = ? AND position = 1',
                         (receipts[1].receipt_id, target.identity.number))
        with self.assertRaisesRegex(ProvenanceError, 'receipt identity mismatch'):
            readback(open_archive(path), target.identity.number)
