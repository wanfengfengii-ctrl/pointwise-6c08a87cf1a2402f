import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from freeze import BoundaryPoint, close_day, open_successor, read_revision
from intake import (
    TimedTransfer,
    add_node,
    create_draft,
    open_source,
    read_draft_snapshot,
    record,
    record_timed,
    withdraw_record,
)
from publish import (
    decode_package,
    decode_timed_package,
    encode_timed_package,
    publish_pack,
    publish_timed_pack,
)
from results import export_excerpt, fetch, issue_timed, open_night
from review import (
    ReviewRequest,
    SampleRequest,
    import_pack,
    import_timed_pack,
)
from samples import BATCH, FRONT, REAR, SAMPLES


def _seed(db, station, draft, rows, points):
    key = create_draft(db, station, BATCH, draft).key
    nodes = {point.node for point in points}
    nodes.update(node for _, start, end, _, _ in rows for node in (start, end))
    for node in sorted(nodes):
        add_node(db, key, node)
    for row in rows:
        record_timed(db, key, *row)
    return close_day(db, key, draft, points).key


class TestTimedIntake(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        self.db = open_source(directory / 'source.db')
        self.addCleanup(self.db.close)
        self.key = create_draft(self.db, FRONT, BATCH, 'D1').key
        add_node(self.db, self.key, 'A')
        add_node(self.db, self.key, 'B')

    def test_timed_record_shape_and_validation(self):
        saved = record_timed(self.db, self.key, 'r1', 'A', 'B', 3, 9)
        self.assertEqual(saved, TimedTransfer('r1', 'A', 'B', 3, 9))
        self.assertEqual((saved.record_id, saved.start, saved.end, saved.depart, saved.arrive),
                         ('r1', 'A', 'B', 3, 9))
        for invalid in ((-1, 0), (2, 1), (True, 1), (0, False), (1.0, 2), ('0', 1)):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, '^INVALID_TIME$'):
                record_timed(self.db, self.key, f'bad-{invalid}', 'A', 'B', *invalid)
        self.assertEqual(len(read_draft_snapshot(self.db, self.key).transfers), 1)

    def test_replay_and_conflict_share_number_space(self):
        first = record_timed(self.db, self.key, 'r1', 'A', 'B', 0, 1)
        self.assertEqual(record_timed(self.db, self.key, 'r1', 'A', 'B', 0, 1), first)
        for changed in (('A', 'B', 0, 2), ('A', 'B', 1, 1)):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, '^RECORD_CONFLICT$'):
                record_timed(self.db, self.key, 'r1', *changed)
        with self.assertRaisesRegex(ValueError, '^RECORD_CONFLICT$'):
            record(self.db, self.key, 'r1', 'A', 'B')
        record(self.db, self.key, 'plain', 'A', 'B')
        with self.assertRaisesRegex(ValueError, '^RECORD_CONFLICT$'):
            record_timed(self.db, self.key, 'plain', 'A', 'B', 0, 0)

    def test_withdraw_and_redraft_preserves_times(self):
        record_timed(self.db, self.key, 'r1', 'A', 'B', 4, 8)
        withdraw_record(self.db, self.key, 'r1')
        record_timed(self.db, self.key, 'r1', 'A', 'B', 5, 9)
        points = (BoundaryPoint('a', 'A'), BoundaryPoint('b', 'B', 'out', REAR))
        frozen = close_day(self.db, self.key, 'R1', points)
        self.assertEqual(frozen.timed_transfers, (TimedTransfer('r1', 'A', 'B', 5, 9),))
        successor = open_successor(self.db, (FRONT, BATCH, 'R1'), 'D2')
        snapshot = read_draft_snapshot(self.db, successor.key)
        self.assertEqual(snapshot.transfers, (TimedTransfer('r1', 'A', 'B', 5, 9),))
        refrozen = read_revision(self.db, (FRONT, BATCH, 'R1'))
        self.assertEqual(refrozen.transfers, frozen.transfers)


class TestTimedPackages(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        self.root = Path(directory)
        self.front_db = open_source(self.root / 'prep.db')
        self.rear_db = open_source(self.root / 'lab.db')
        self.addCleanup(self.front_db.close)
        self.addCleanup(self.rear_db.close)
        self.front_points = (
            BoundaryPoint('a1', 'a1'), BoundaryPoint('a2', 'a2'),
            BoundaryPoint('p', 'p', 'out', REAR),
        )
        self.rear_points = (
            BoundaryPoint('p', 'p', 'in', FRONT),
            BoundaryPoint('b1', 'b1'), BoundaryPoint('b2', 'b2'),
        )
        self.front_rows = (
            ('feed-A', 'a1', 'hub', 0, 5), ('feed-B', 'a2', 'hub', 0, 4),
            ('fast', 'hub', 'p', 5, 10), ('late', 'hub', 'p', 5, 20),
        )
        self.rear_rows = (
            ('lab-A', 'p', 'b1', 20, 25), ('lab-B', 'p', 'b2', 20, 26),
        )

    def _publish_both(self):
        fkey = _seed(self.front_db, FRONT, 'F1', self.front_rows, self.front_points)
        rkey = _seed(self.rear_db, REAR, 'L1', self.rear_rows, self.rear_points)
        return publish_timed_pack(self.front_db, fkey), publish_timed_pack(self.rear_db, rkey)

    def test_versions_coexist_and_private_facts_stay_home(self):
        fkey = _seed(self.front_db, FRONT, 'F1', self.front_rows, self.front_points)
        timed_payload = publish_timed_pack(self.front_db, fkey)
        v1_payload = publish_pack(self.front_db, fkey)
        self.assertEqual(publish_timed_pack(self.front_db, fkey), timed_payload)
        self.assertEqual(publish_pack(self.front_db, fkey), v1_payload)
        self.assertNotEqual(decode_timed_package(timed_payload).pack_id,
                            decode_package(v1_payload).pack_id)
        for secret in ('hub', 'feed-A', 'fast', 'late'):
            self.assertNotIn(secret, timed_payload)
        timed = decode_timed_package(timed_payload)
        self.assertEqual(timed.evidence.singles[('a1', 'p')].front, (10, 20))
        self.assertEqual(timed.evidence.singles[('a1', 'p')].rear, (0,))

    def test_mutually_exclusive_records_gate_joint_times(self):
        payload, _ = self._publish_both()
        evidence = decode_timed_package(payload).evidence
        pair = evidence.pairs[(('a1', 'p'), ('a2', 'p'))]
        # Only the parallel 10/20 split is jointly achievable; (20, 20) is not.
        self.assertEqual(pair.front, ((10, 20), (20, 10)))

    def test_parallel_same_endpoint_records_enable_same_moment_pair(self):
        _seed(self.front_db, FRONT, 'F1', self.front_rows, self.front_points)
        key = FRONT, BATCH, 'F1'
        successor = open_successor(self.front_db, key, 'F2')
        record_timed(self.front_db, successor.key, 'late-2', 'hub', 'p', 5, 20)
        close_day(self.front_db, successor.key, 'F2', self.front_points)
        payload = publish_timed_pack(self.front_db, (FRONT, BATCH, 'F2'))
        pair = decode_timed_package(payload).evidence.pairs[(('a1', 'p'), ('a2', 'p'))]
        self.assertIn((20, 20), pair.front)

    def test_import_replay_conflict_and_rejection(self):
        front_payload, rear_payload = self._publish_both()
        with closing(open_night(self.root / 'night.db')) as night:
            front_id = import_timed_pack(night, front_payload.encode('utf-8'))
            spaced = json.dumps(json.loads(front_payload), indent=2)
            self.assertEqual(import_timed_pack(night, spaced), front_id)
            altered = json.loads(front_payload)
            altered['id'] = front_id
            altered['station'] = 'other-station'
            with self.assertRaisesRegex(ValueError, '^PACKAGE_ID_CONFLICT$'):
                import_timed_pack(night, json.dumps(altered))
            v1_id = import_pack(night, publish_pack(self.front_db, (FRONT, BATCH, 'F1')))
            self.assertNotEqual(v1_id, front_id)
            invalid = json.loads(front_payload)
            invalid['eligibility']['singles'][0]['front'] = [True]
            with self.assertRaisesRegex(ValueError, '^INVALID_EVIDENCE$'):
                import_timed_pack(night, json.dumps(invalid))
            bad_version = json.loads(front_payload)
            bad_version['version'] = 1
            with self.assertRaisesRegex(ValueError, '^INVALID_PACKAGE$'):
                import_timed_pack(night, json.dumps(bad_version))
            self.assertEqual(
                night.execute('SELECT COUNT(*) FROM imported_packages').fetchone(), (2,))
            self.assertTrue(rear_payload)


class TestTimedIssue(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        self.root = Path(directory)
        self.front_db = open_source(self.root / 'prep.db')
        self.rear_db = open_source(self.root / 'lab.db')
        self.addCleanup(self.front_db.close)
        self.addCleanup(self.rear_db.close)
        self.front_points = (
            BoundaryPoint('a1', 'a1'), BoundaryPoint('a2', 'a2'),
            BoundaryPoint('broken', 'broken'),
            BoundaryPoint('p', 'p', 'out', REAR),
        )
        self.rear_points = (
            BoundaryPoint('p', 'p', 'in', FRONT),
            BoundaryPoint('b1', 'b1'), BoundaryPoint('b2', 'b2'),
        )
        self.front_rows = (
            ('feed-A', 'a1', 'hub', 0, 5), ('feed-B', 'a2', 'hub', 0, 4),
            ('fast', 'hub', 'p', 5, 10), ('late-1', 'hub', 'p', 5, 20),
            ('stop', 'broken', 'turn', 0, 12),
            ('reverse', 'turn', 'p', 11, 20),
        )
        self.simultaneous = (('lab-A', 'p', 'b1', 20, 25), ('lab-B', 'p', 'b2', 20, 26))
        self.staggered = (('lab-A', 'p', 'b1', 11, 25), ('lab-B', 'p', 'b2', 21, 26))

    def _import(self, rows, night):
        fkey = _seed(self.front_db, FRONT, 'F1', self.front_rows, self.front_points)
        rkey = _seed(self.rear_db, REAR, 'L1', rows, self.rear_points)
        front = import_timed_pack(night, publish_timed_pack(self.front_db, fkey))
        rear = import_timed_pack(night, publish_timed_pack(self.rear_db, rkey))
        return front, rear

    def test_singles_double_refusal_and_lexicographic_choice(self):
        night_path = self.root / 'night.db'
        with closing(open_night(night_path)) as night:
            front, rear = self._import(self.simultaneous, night)
            for sample in SAMPLES:
                signed = issue_timed(night, ReviewRequest(front, rear, (sample,)), 2)
                self.assertEqual(signed.handoffs, ((20, 20),))
                self.assertEqual(signed.max_wait, 2)
            before = self._counts(night)
            with self.assertRaisesRegex(ValueError, '^NO_COMMON_EVIDENCE$'):
                issue_timed(night, ReviewRequest(front, rear, SAMPLES), 2)
            self.assertEqual(self._counts(night), before)
            broad = issue_timed(night, ReviewRequest(front, rear, SAMPLES), 10)
            self.assertEqual(broad.interfaces, ('p', 'p'))
            self.assertEqual(broad.handoffs, ((10, 20), (20, 20)))

    def test_staggered_bounds_and_swapped_order(self):
        with closing(open_night(self.root / 'night.db')) as night:
            front, rear = self._import(self.staggered, night)
            signed = issue_timed(night, ReviewRequest(front, rear, SAMPLES), 1)
            self.assertEqual(signed.interfaces, ('p', 'p'))
            self.assertEqual(signed.handoffs, ((10, 11), (20, 21)))
            swapped = issue_timed(night, ReviewRequest(front, rear, SAMPLES[::-1]), 1)
            self.assertEqual(swapped.samples, SAMPLES[::-1])
            self.assertEqual(swapped.handoffs, ((20, 21), (10, 11)))
            with self.assertRaisesRegex(ValueError, '^NO_COMMON_EVIDENCE$'):
                issue_timed(night, ReviewRequest(front, rear, SAMPLES), 0)

    def test_invalid_inputs_and_v1_parent(self):
        with closing(open_night(self.root / 'night.db')) as night:
            front, rear = self._import(self.simultaneous, night)
            v1 = import_pack(night, publish_pack(self.front_db, (FRONT, BATCH, 'F1')))
            for bad_wait in (-1, False, True, 1.0, '2'):
                with self.subTest(bad_wait=bad_wait):
                    with self.assertRaises(ValueError):
                        issue_timed(night, ReviewRequest(front, rear, SAMPLES), bad_wait)
            with self.assertRaises(ValueError):
                issue_timed(night, ReviewRequest(v1, rear, SAMPLES), 1)
            with self.assertRaises(ValueError):
                issue_timed(night, ReviewRequest(front, v1, SAMPLES), 1)
            broken = SampleRequest('broken-sample', 'broken', 'b1')
            with self.assertRaisesRegex(ValueError, '^NO_COMMON_EVIDENCE$'):
                issue_timed(night, ReviewRequest(front, rear, (broken,)), 2)
            self.assertEqual(self._counts(night), (0, 0))

    def test_atomic_failure_leaves_no_trace_and_reopen_fetches_snapshot(self):
        night_path = self.root / 'night.db'
        with closing(open_night(night_path)) as night:
            front, rear = self._import(self.staggered, night)
            request = ReviewRequest(front, rear, SAMPLES)
            night.execute('''
                CREATE TEMP TRIGGER fail_timed_receipt BEFORE INSERT ON receipts
                BEGIN SELECT RAISE(ABORT, 'fixture receipt write failed'); END
            ''')
            with self.assertRaisesRegex(sqlite3.IntegrityError,
                                        '^fixture receipt write failed$'):
                issue_timed(night, request, 1)
            self.assertFalse(night.in_transaction)
            night.execute('DROP TRIGGER fail_timed_receipt')
            signed = issue_timed(night, request, 1)
            excerpt = export_excerpt(night, signed.result_id)
        with closing(open_night(night_path)) as night:
            self.assertEqual(fetch(night, signed.result_id), signed)
            self.assertEqual(export_excerpt(night, signed.result_id), excerpt)
            body = json.loads(excerpt)
            self.assertEqual(body['max_wait'], 1)
            self.assertEqual(
                [(row['front_arrive'], row['rear_depart']) for row in body['samples']],
                [(10, 11), (20, 21)],
            )

    @staticmethod
    def _counts(db):
        return tuple(db.execute(query).fetchone()[0] for query in (
            'SELECT COUNT(*) FROM issued_results', 'SELECT COUNT(*) FROM receipts'))


if __name__ == '__main__':
    unittest.main()
