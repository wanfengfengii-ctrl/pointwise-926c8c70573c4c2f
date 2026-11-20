from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


def encode_quantity(value: int) -> str:
    if type(value) is not int or value < 0:
        raise ValueError('quantity must be a nonnegative integer')
    return format(value, 'x')


def decode_quantity(value: str) -> int:
    if not isinstance(value, str) or not value:
        raise ValueError('invalid stored quantity')
    number = int(value, 16)
    if encode_quantity(number) != value:
        raise ValueError('noncanonical stored quantity')
    return number


class StationDatabase:
    def __init__(self, path, *, read_only=False, before_commit=None):
        self.path = Path(path).absolute()
        self.read_only = read_only
        self.before_commit = before_commit

    def _connect(self):
        target = self.path.as_uri() + '?mode=ro' if self.read_only else self.path
        conn = sqlite3.connect(target, uri=self.read_only, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys = ON')
        except BaseException:
            conn.close()
            raise
        return conn

    def initialize(self, *schemas):
        with self.transaction('initialize') as conn:
            for schema in schemas:
                for statement in schema:
                    conn.execute(statement)

    @contextmanager
    def transaction(self, operation):
        if self.read_only:
            raise ValueError('the archive is read-only')
        with closing(self._connect()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                yield conn
                if self.before_commit is not None and conn.total_changes:
                    self.before_commit(operation, conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def read_snapshot(self):
        with closing(self._connect()) as conn:
            conn.execute('PRAGMA query_only = ON')
            conn.execute('BEGIN')
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
