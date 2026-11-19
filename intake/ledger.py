from dataclasses import dataclass

from .days import DraftDay, load_draft, require_open_draft
from .nodes import insert_nodes, list_nodes, require_endpoints
from .source_store import source_transaction
from .timed_transfers import (
    TimedTransfer,
    delete_timed_transfer,
    find_timed_transfer,
    insert_timed_transfer,
    list_timed_transfers,
    validate_window,
)
from .transfers import Transfer, accept_transfer, delete_transfer, list_transfers


@dataclass(frozen=True)
class DraftSnapshot:
    day: DraftDay
    nodes: tuple[str, ...]
    transfers: tuple[Transfer | TimedTransfer, ...]


def record(db, key, record_id, start, end):
    with source_transaction(db):
        require_open_draft(db, key)
        require_endpoints(db, key, start, end)
        return accept_transfer(db, key, Transfer(record_id, start, end))


def record_timed(db, key, record_id, start, end, depart, arrive):
    with source_transaction(db):
        require_open_draft(db, key)
        require_endpoints(db, key, start, end)
        validate_window(depart, arrive)
        timed_row = find_timed_transfer(db, key, record_id)
        if timed_row is not None:
            if timed_row != (record_id, start, end, depart, arrive):
                raise ValueError('RECORD_CONFLICT')
            return TimedTransfer(*timed_row)
        plain_row = db.execute('''
            SELECT start, end FROM transfers
            WHERE station = ? AND batch = ? AND draft = ? AND record_id = ?
        ''', (key[0], key[1], key[2], record_id)).fetchone()
        if plain_row is not None:
            raise ValueError('RECORD_CONFLICT')
        return insert_timed_transfer(db, key, TimedTransfer(record_id, start, end, depart, arrive))


def withdraw_record(db, key, record_id):
    with source_transaction(db):
        require_open_draft(db, key)
        if delete_timed_transfer(db, key, record_id):
            return
        delete_transfer(db, key, record_id)


def _load_snapshot(db, key):
    return DraftSnapshot(
        load_draft(db, key),
        list_nodes(db, key),
        list_transfers(db, key) + list_timed_transfers(db, key),
    )


def read_draft_snapshot(db, key):
    if db.in_transaction:
        return _load_snapshot(db, key)
    db.execute('BEGIN')
    with db:
        return _load_snapshot(db, key)


def populate_draft(db, key, nodes, transfers):
    require_open_draft(db, key)
    insert_nodes(db, key, nodes)
    for transfer in transfers:
        require_endpoints(db, key, transfer.start, transfer.end)
        if isinstance(transfer, TimedTransfer):
            insert_timed_transfer(db, key, transfer)
        else:
            accept_transfer(db, key, transfer)
