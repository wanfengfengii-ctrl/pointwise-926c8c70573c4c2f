from dataclasses import dataclass


DEVICE_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS devices (
        device_id TEXT PRIMARY KEY NOT NULL
    )''',
)


def require_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonempty identifier')
    return value


@dataclass(frozen=True)
class Device:
    device_id: str


class DeviceRegistry:
    def __init__(self, conn):
        self.conn = conn

    def register(self, device_id):
        require_identifier(device_id, 'device_id')
        self.conn.execute(
            'INSERT INTO devices (device_id) VALUES (?) ON CONFLICT (device_id) DO NOTHING',
            (device_id,),
        )
        return self.require(device_id)

    def require(self, device_id):
        require_identifier(device_id, 'device_id')
        row = self.conn.execute(
            'SELECT device_id FROM devices WHERE device_id = ?', (device_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f'unknown device {device_id!r}')
        return Device(row['device_id'])
