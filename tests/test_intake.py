from dataclasses import replace
import sqlite3

from station.intake.receive import accept
from station.intake.store import EvidenceStore
from station.setup.bootstrap import open_archive
from station.setup.storage import StationDatabase
from tests.support import StationCase


class TestIntake(StationCase):
    def test_manual_read_clear(self):
        shift = self.begin()
        recording = shift.recording_id
        self.station.count(recording, 7)
        first = self.station.inspect(recording, 'first')
        zero = self.station.inspect(recording, 'zero')
        self.assertEqual([(item.action_id, item.sequence, item.pulses) for item in (first, zero)],
                         [('first', 1, 7), ('zero', 2, 0)])
        first_receipt, zero_receipt = self.deliver(first, zero)
        self.assertEqual(self.station.accept(first), first_receipt)
        self.assertNotEqual(first_receipt.receipt_id, zero_receipt.receipt_id)
        with self.station.database.read_snapshot() as conn:
            entries = EvidenceStore(conn).list_for_recording(recording)
            self.assertEqual([entry.evidence.pulses for entry in entries], [7, 0])
            self.assertEqual([entry.receipt for entry in entries], [first_receipt, zero_receipt])

    def test_preserves_original(self):
        recording = self.begin().recording_id
        self.station.count(recording, 7)
        original = self.station.inspect(recording, 'first')
        receipt = self.station.accept(original)
        with self.assertRaisesRegex(ValueError, 'identity or content conflict'):
            self.station.accept(replace(original, pulses=8))
        self.assertEqual(accept(StationDatabase(self.path), original), receipt)
        with open_archive(self.path).read_snapshot() as conn:
            entries = EvidenceStore(conn).list_for_recording(recording)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].evidence.pulses, 7)
            self.assertEqual(entries[0].receipt, receipt)

    def test_raw_register(self):
        first = self.begin().recording_id
        self.station.configure('spare', 'r1', 3)
        second = self.station.begin('spare-day', 'spare', 'r1', 14).recording_id
        register = self.sessions.require(first).register
        self.station.count(first, 7)
        self.station.count(second, 4)
        for bad in (-1, True, 2.5, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.station.count(first, bad)
        with self.assertRaises(LookupError):
            self.station.count('unknown', 1)
        self.assertEqual(register.read(), 7)
        self.assertEqual(register.read(), 7)
        self.assertEqual(register.read_clear(), 7)
        self.assertEqual(register.read_clear(), 0)
        self.assertEqual(self.sessions.require(second).register.read(), 4)

    def test_pulse_between_primitives(self):
        recording = self.begin().recording_id
        register = self.sessions.require(recording).register
        self.station.count(recording, 7)
        register.after('read', lambda: self.station.count(recording, 1))
        self.assertEqual(register.read(), 7)
        register.after('read_clear', lambda: self.station.count(recording, 2))
        first = self.station.inspect(recording, 'first')
        second = self.station.inspect(recording, 'second')
        terminal = self.station.stop(recording, 'stop')
        self.assertEqual([first.pulses, second.pulses, terminal.pulses], [8, 2, 0])
        self.deliver(terminal, second, first)
        self.assertEqual(self.station.publish('day').report.totals.pulses, 10)

    def test_action_replay(self):
        recording = self.begin().recording_id
        self.station.count(recording, 7)
        first = self.station.inspect(recording, 'first')
        self.station.count(recording, 3)
        self.assertIs(self.station.inspect(recording, 'first'), first)
        with self.assertRaisesRegex(ValueError, 'action kind conflict'):
            self.station.stop(recording, 'first')
        second = self.station.inspect(recording, 'second')
        self.assertEqual((first.sequence, first.pulses, second.sequence, second.pulses), (1, 7, 2, 3))

    def test_binding(self):
        recording = self.begin().recording_id
        other = self.station.stop(recording, 'end')
        self.begin('evening')
        for wrong in (replace(other, shift_id='evening'), replace(other, device_id='elsewhere'),
                      replace(other, recording_id='another-recording')):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, 'frozen shift binding'):
                self.station.accept(wrong)
        with self.station.database.read_snapshot() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM evidence').fetchone()[0], 0)
        self.assertEqual(self.station.accept(other).recording_id, recording)

    def test_checked_keys(self):
        recording = self.begin().recording_id
        original = self.station.inspect(recording, 'first')
        receipt = self.station.accept(original)
        for wrong in (replace(original, sequence=2), replace(original, action_id='different')):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, 'identity or content conflict'):
                self.station.accept(wrong)
        with open_archive(self.path).read_snapshot() as conn:
            store = EvidenceStore(conn)
            self.assertEqual(store.by_sequence(recording, 1), store.by_action(recording, 'first'))
            self.assertEqual(store.by_action(recording, 'first').receipt, receipt)
            self.assertIsNone(store.by_sequence(recording, 2))
            self.assertIsNone(store.by_action(recording, 'different'))

    def test_intake_rollback(self):
        recording = self.begin().recording_id
        first = self.station.inspect(recording, 'first')
        first_receipt = self.station.accept(first)
        self.station.count(recording, 3)
        second = self.station.inspect(recording, 'second')

        def observe(conn):
            pending = EvidenceStore(conn).by_action(recording, 'second')
            self.assertEqual((pending.evidence.pulses, pending.receipt.sequence), (3, 2))

        self.fault.arm('accept', observe)
        with self.assertRaises(sqlite3.IntegrityError):
            self.station.accept(second)
        with open_archive(self.path).read_snapshot() as conn:
            for table in ('evidence', 'receipts'):
                query = 'SELECT count(*) FROM ' + table + ' WHERE recording_id = ? AND sequence = ?'
                self.assertEqual(conn.execute(query, (recording, '2')).fetchone()[0], 0)
            self.assertEqual(EvidenceStore(conn).by_action(recording, 'first').receipt, first_receipt)
        receipt = self.station.accept(second)
        self.assertEqual(receipt.sequence, 2)
        self.assertEqual(self.station.accept(second), receipt)
