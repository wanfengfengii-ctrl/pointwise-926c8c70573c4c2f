from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from station.meter.register import PulseRegister
from station.meter.session import SessionDirectory
from station.setup.bootstrap import open_station


class CommitFault:
    def __init__(self):
        self._armed = None

    def arm(self, operation, observe=None):
        if self._armed is not None:
            raise AssertionError('commit fault already armed')
        self._armed = (operation, observe)

    def __call__(self, operation, conn):
        if self._armed is None or self._armed[0] != operation:
            return
        _, observe = self._armed
        self._armed = None
        if observe is not None:
            observe(conn)
        conn.execute('CREATE TEMP TABLE fault_parent (id INTEGER PRIMARY KEY)')
        conn.execute(
            '''CREATE TEMP TABLE fault_child (
                   id INTEGER REFERENCES fault_parent (id) DEFERRABLE INITIALLY DEFERRED
               )'''
        )
        conn.execute('INSERT INTO fault_child (id) VALUES (1)')


class InterleavedRegister(PulseRegister):
    def __init__(self):
        super().__init__()
        self._scheduled = None

    def after(self, operation, inject):
        if operation not in ('read', 'read_clear') or self._scheduled is not None:
            raise ValueError('invalid register injection')
        self._scheduled = (operation, inject)

    def _inject_after(self, operation):
        if self._scheduled is not None and self._scheduled[0] == operation:
            _, inject = self._scheduled
            self._scheduled = None
            inject()

    def read(self):
        value = super().read()
        self._inject_after('read')
        return value

    def read_clear(self):
        value = super().read_clear()
        self._inject_after('read_clear')
        return value


class StationCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'station.sqlite3'
        self.fault = CommitFault()
        self.sessions = SessionDirectory(register_factory=InterleavedRegister)
        self.station = open_station(self.path, sessions=self.sessions, before_commit=self.fault)
        self.station.configure('bench', 'r1', 2)

    def begin(self, shift_id='day', limit=14):
        return self.station.begin(shift_id, 'bench', 'r1', limit)

    def deliver(self, *evidence):
        return tuple(self.station.accept(item) for item in evidence)

    def backup_archive(self):
        target_path = self.path.with_name('archive-copy.sqlite3')
        with closing(sqlite3.connect(self.path)) as source:
            with closing(sqlite3.connect(target_path)) as target:
                source.backup(target)
        return target_path
