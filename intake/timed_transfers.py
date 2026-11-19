from dataclasses import dataclass

from .days import require_identity, validate_key


@dataclass(frozen=True)
class TimedTransfer:
    record_id: str
    start: str
    end: str
    depart: int
    arrive: int

    def plain(self):
        from .transfers import Transfer

        return Transfer(self.record_id, self.start, self.end)


def require_second(value):
    # Times are non-negative integer seconds; booleans are not integers here.
    if type(value) is not int or value < 0:
        raise ValueError('INVALID_TIME')
    return value


def validate_window(depart, arrive):
    require_second(depart)
    require_second(arrive)
    if depart > arrive:
        raise ValueError('INVALID_TIME')


def insert_timed_transfer(db, key, transfer):
    validate_window(transfer.depart, transfer.arrive)
    db.execute('''
        INSERT INTO timed_transfers
            (station, batch, draft, record_id, start, end, depart, arrive)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (*validate_key(key), transfer.record_id, transfer.start, transfer.end,
          transfer.depart, transfer.arrive))
    return transfer


def find_timed_transfer(db, key, record_id):
    return db.execute('''
        SELECT record_id, start, end, depart, arrive FROM timed_transfers
        WHERE station = ? AND batch = ? AND draft = ? AND record_id = ?
    ''', (*validate_key(key), require_identity(record_id))).fetchone()


def list_timed_transfers(db, key):
    rows = db.execute('''
        SELECT record_id, start, end, depart, arrive FROM timed_transfers
        WHERE station = ? AND batch = ? AND draft = ? ORDER BY record_id
    ''', validate_key(key))
    return tuple(TimedTransfer(*row) for row in rows)


def delete_timed_transfer(db, key, record_id):
    cursor = db.execute('''
        DELETE FROM timed_transfers
        WHERE station = ? AND batch = ? AND draft = ? AND record_id = ?
    ''', (*validate_key(key), require_identity(record_id)))
    return cursor.rowcount
