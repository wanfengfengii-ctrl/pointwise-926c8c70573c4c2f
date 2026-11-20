from dataclasses import replace
import sqlite3

from station.audit.readback import readback
from station.intake.store import EvidenceStore
from station.report.archive import ReportArchive
from station.setup.bootstrap import open_archive
from tests.support import StationCase


class TestReporting(StationCase):
    def test_waits_for_missing_inspection(self):
        recording = self.begin().recording_id
        self.station.count(recording, 7)
        first = self.station.inspect(recording, 'first')
        self.station.count(recording, 3)
        terminal = self.station.stop(recording, 'stop')
        self.station.accept(terminal)
        waiting = self.station.publish('day')
        self.assertEqual((waiting.status, waiting.missing_sequences, waiting.report), ('missing', (1,), None))
        self.station.accept(first)
        report = self.station.publish('day').report
        self.assertEqual([ref.action_id for ref in report.references], ['first', 'stop'])
        self.assertEqual((report.totals.pulses, report.totals.microliters), (10, 20))
        self.assertTrue(report.decision.over_limit)

    def test_retry_and_threshold(self):
        recording = self.begin().recording_id
        self.station.count(recording, 7)
        first = self.station.inspect(recording, 'first')
        terminal = self.station.stop(recording, 'stop')
        receipts = self.deliver(first, terminal)
        pending_numbers = []

        def observe(conn):
            pending = ReportArchive(conn).find_by_shift('day')
            self.assertIsNotNone(pending)
            pending_numbers.append(pending.identity.number)
            self.assertEqual([ref.receipt_id for ref in pending.references], [item.receipt_id for item in receipts])

        self.fault.arm('publish', observe)
        with self.assertRaises(sqlite3.IntegrityError):
            self.station.publish('day')
        self.assertEqual(len(pending_numbers), 1)
        with open_archive(self.path).read_snapshot() as conn:
            self.assertIsNone(ReportArchive(conn).find_by_number(pending_numbers[0]))
            self.assertEqual(conn.execute('SELECT count(*) FROM report_refs').fetchone()[0], 0)
            entries = EvidenceStore(conn).list_for_recording(recording)
            self.assertEqual([item.receipt for item in entries], list(receipts))
        outcome = self.station.publish('day')
        self.assertEqual(outcome.status, 'signed')
        self.assertEqual((outcome.report.totals.microliters, outcome.report.decision.over_limit), (14, False))
        self.assertEqual(self.station.publish('day'), outcome)
        self.assertEqual(readback(open_archive(self.path), outcome.report.identity.number).report, outcome.report)
        with open_archive(self.path).read_snapshot() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM reports').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM report_refs').fetchone()[0], 2)

    def test_before_stop(self):
        recording = self.begin().recording_id
        first = self.station.inspect(recording, 'first')
        self.station.accept(first)
        for step in ('active', 'terminal-not-delivered'):
            if step == 'terminal-not-delivered':
                self.station.stop(recording, 'stop')
            outcome = self.station.publish('day')
            self.assertEqual((outcome.status, outcome.missing_sequences, outcome.report), ('awaiting_stop', (), None))

    def test_stop_replay(self):
        recording = self.begin().recording_id
        first = self.station.inspect(recording, 'first')
        self.station.count(recording, 4)
        terminal = self.station.stop(recording, 'stop')
        self.assertEqual((terminal.sequence, terminal.terminal_sequence, terminal.pulses), (2, 2, 4))
        self.assertFalse(self.sessions.require(recording).is_active())
        self.assertEqual(self.sessions.require(recording).register.read(), 0)
        self.assertIs(self.station.stop(recording, 'stop'), terminal)
        self.assertIs(self.station.inspect(recording, 'first'), first)
        for invoke, args in ((self.station.count, (1,)), (self.station.inspect, ('new',)),
                             (self.station.stop, ('new',)), (self.station.inspect, ('stop',))):
            with self.subTest(operation=invoke.__name__, args=args), self.assertRaises(ValueError):
                invoke(recording, *args)

    def test_missing_zero(self):
        recording = self.begin().recording_id
        self.station.count(recording, 7)
        first = self.station.inspect(recording, 'first')
        second = self.station.inspect(recording, 'zero')
        terminal = self.station.stop(recording, 'stop')
        self.station.accept(terminal)
        one = self.station.publish('day')
        self.assertEqual((one.status, one.missing_sequences, one.report), ('missing', (1, 2), None))
        self.station.accept(first)
        two = self.station.publish('day')
        self.assertEqual((two.status, two.missing_sequences, two.report), ('missing', (2,), None))
        zero_receipt = self.station.accept(second)
        report = self.station.publish('day').report
        self.assertEqual([ref.sequence for ref in report.references], [1, 2, 3])
        self.assertEqual(report.references[1].receipt_id, zero_receipt.receipt_id)
        result = readback(open_archive(self.path), report.identity.number)
        self.assertEqual([item.evidence.pulses for item in result.provenance.entries], [7, 0, 0])
        self.assertEqual(result.report.totals.microliters, 14)

    def test_terminal_bounds(self):
        recording = self.begin().recording_id
        first = self.station.inspect(recording, 'first')
        terminal = self.station.stop(recording, 'stop')
        terminal_receipt = self.station.accept(terminal)
        for invalid in (replace(first, sequence=3, action_id='late'),
                        replace(terminal, sequence=3, terminal_sequence=3, action_id='second-stop'),
                        replace(terminal, sequence=1, terminal_sequence=1, action_id='early-stop')):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'stop boundary'):
                self.station.accept(invalid)
        self.assertEqual(self.station.accept(terminal), terminal_receipt)
        self.station.accept(first)
        self.assertEqual(self.station.publish('day').status, 'signed')

    def test_terminal_cannot_exclude_accepted_original(self):
        recording = self.begin().recording_id
        first = self.station.inspect(recording, 'first')
        second = self.station.inspect(recording, 'second')
        terminal = self.station.stop(recording, 'stop')
        self.station.accept(second)
        too_early = replace(terminal, sequence=1, terminal_sequence=1)
        with self.assertRaisesRegex(ValueError, 'excludes previously received evidence'):
            self.station.accept(too_early)
        self.deliver(terminal, first)
        self.assertEqual(self.station.publish('day').status, 'signed')

    def test_integer_exactness(self):
        huge = (1 << 68) + 3
        for number, (pulses, limit, expected_amount, over) in enumerate((
            (7, 13, 14, True), (7, 14, 14, False), (7, 15, 14, False),
            (0, 0, 0, False), (huge, (1 << 69) + 6, (1 << 69) + 6, False),
        )):
            with self.subTest(pulses=pulses, limit=limit):
                shift_id = f'quantities-{number}'
                recording = self.begin(shift_id, limit).recording_id
                self.station.count(recording, pulses)
                self.station.accept(self.station.stop(recording, 'stop'))
                signed = self.station.publish(shift_id).report
                result = readback(open_archive(self.path), signed.identity.number)
                self.assertEqual(result.report.totals.pulses, pulses)
                self.assertEqual(result.report.totals.microliters, expected_amount)
                self.assertEqual(result.report.decision.limit_microliters, limit)
                self.assertIs(result.report.decision.over_limit, over)

    def test_fixed_report(self):
        recording = self.begin().recording_id
        for sequence in range(1, 18):
            self.station.count(recording, sequence)
            self.station.accept(self.station.inspect(recording, f'check-{sequence}'))
        self.station.accept(self.station.stop(recording, 'stop'))
        original = self.station.publish('day').report
        replay = self.station.publish('day').report
        self.assertEqual(replay, original)
        self.assertEqual([ref.sequence for ref in replay.references], list(range(1, 19)))
        self.assertEqual((replay.totals.pulses, replay.totals.microliters), (153, 306))
        self.assertEqual(readback(open_archive(self.path), replay.identity.number).report, original)
