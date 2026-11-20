import sqlite3

from station.setup.bootstrap import open_archive, open_station
from station.setup.calibration import CalibrationCatalog
from station.setup.shifts import ShiftRegistry
from tests.support import StationCase


class TestSetup(StationCase):
    def test_freezes_calibration(self):
        shift = self.begin()
        self.station.configure('bench', 'r2', 3)
        with self.station.database.read_snapshot() as conn:
            self.assertEqual(ShiftRegistry(conn).find('day'), shift)
        self.assertEqual((shift.revision, shift.microliters_per_pulse, shift.limit_microliters), ('r1', 2, 14))
        self.station.count(shift.recording_id, 7)
        self.deliver(self.station.stop(shift.recording_id, 'stop'))
        self.assertEqual(self.station.publish('day').report.totals.microliters, 14)

    def test_conflicting_begin(self):
        shift = self.begin()
        self.station.count(shift.recording_id, 7)
        self.station.configure('bench', 'r2', 3)
        for args in (('bench', 'r2', 14), ('bench', 'r1', 15), ('other', 'r1', 14)):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'shift parameters conflict'):
                self.station.begin('day', *args)
        self.assertEqual(self.begin(), shift)
        self.assertEqual(self.station.inspect(shift.recording_id, 'first').pulses, 7)

    def test_config_replay(self):
        first = self.station.configure('bench', 'r1', 2)
        self.assertEqual(self.station.configure('bench', 'r1', 2), first)
        for invalid in (0, -1, 1.5, True, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.station.configure('bad-device', 'r1', invalid)
        with self.assertRaisesRegex(ValueError, 'calibration revision conflict'):
            self.station.configure('bench', 'r1', 3)
        with self.station.database.read_snapshot() as conn:
            self.assertEqual(CalibrationCatalog(conn).require('bench', 'r1'), first)
            self.assertEqual([row[0] for row in conn.execute('SELECT device_id FROM devices')], ['bench'])

    def test_configure_rolls_back_device_and_calibration(self):
        self.fault.arm('configure')
        with self.assertRaises(sqlite3.IntegrityError):
            self.station.configure('spare', 'r1', 6)
        with open_archive(self.path).read_snapshot() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM devices WHERE device_id = 'spare'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM calibrations WHERE device_id = 'spare'").fetchone()[0], 0)
        self.assertEqual(self.station.configure('spare', 'r1', 6).microliters_per_pulse, 6)

    def test_initialization_replay(self):
        shift = self.begin()
        updated = self.station.configure('bench', 'r2', 3)
        reopened = open_station(self.path, sessions=self.sessions)
        reopened = open_station(self.path, sessions=reopened.sessions)
        self.assertEqual(reopened.begin('day', 'bench', 'r1', 14), shift)
        self.assertEqual(reopened.configure('bench', 'r2', 3), updated)
        with self.assertRaisesRegex(ValueError, 'calibration revision conflict'):
            reopened.configure('bench', 'r2', 4)
        with reopened.database.read_snapshot() as conn:
            self.assertEqual(ShiftRegistry(conn).find('day').microliters_per_pulse, 2)
            self.assertEqual(CalibrationCatalog(conn).require('bench', 'r2').microliters_per_pulse, 3)

    def test_begin_replay(self):
        shift = self.begin()
        self.station.count(shift.recording_id, 7)
        self.assertEqual(self.begin(), shift)
        original = self.station.inspect(shift.recording_id, 'first')
        self.assertEqual((original.recording_id, original.sequence, original.pulses), (shift.recording_id, 1, 7))

    def test_device_busy(self):
        first = self.begin()
        with self.assertRaisesRegex(ValueError, 'active shift'):
            self.begin('evening')
        with open_archive(self.path).read_snapshot() as conn:
            self.assertIsNone(ShiftRegistry(conn).find('evening'))
        self.assertTrue(self.sessions.require(first.recording_id).is_active())
        terminal = self.station.stop(first.recording_id, 'end-day')
        second = self.begin('evening')
        self.assertNotEqual(first.recording_id, second.recording_id)
        self.assertEqual(self.sessions.require(second.recording_id).register.read(), 0)
        self.deliver(terminal)
        self.assertEqual(self.station.publish('day').report.shift, first)
        self.assertEqual(self.station.publish('evening').status, 'awaiting_stop')

    def test_begin_rollback(self):
        prepared_ids = []

        def observe(conn):
            row = conn.execute("SELECT recording_id FROM shifts WHERE shift_id = 'day'").fetchone()
            self.assertIsNotNone(row)
            prepared_ids.append(row['recording_id'])
            with self.assertRaises(LookupError):
                self.sessions.require(row['recording_id'])

        self.fault.arm('begin', observe)
        with self.assertRaises(sqlite3.IntegrityError):
            self.begin()
        self.assertEqual(len(prepared_ids), 1)
        with open_archive(self.path).read_snapshot() as conn:
            self.assertIsNone(ShiftRegistry(conn).find('day'))
        with self.assertRaises(LookupError):
            self.sessions.require(prepared_ids[0])
        shift = self.begin()
        self.assertEqual(self.begin(), shift)
        self.assertTrue(self.sessions.require(shift.recording_id).is_active())
        with open_archive(self.path).read_snapshot() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM shifts').fetchone()[0], 1)

    def test_unknown_and_invalid_begin(self):
        with self.assertRaises(LookupError):
            self.station.begin('unknown-device', 'spare', 'r1', 14)
        with self.assertRaises(LookupError):
            self.station.begin('unknown-revision', 'bench', 'absent', 14)
        for limit in (-1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.begin(limit=limit)
        self.assertEqual(self.begin().limit_microliters, 14)
