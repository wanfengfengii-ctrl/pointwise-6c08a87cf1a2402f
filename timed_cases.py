import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from freeze import BoundaryPoint, close_day, open_successor
from intake import add_node, create_draft, open_source, record
from publish import decode_package, publish_pack
from results import export_excerpt, fetch, issue, open_night
from review import ReviewRequest, SampleRequest, import_pack
from samples import BATCH, FRONT, REAR, SAMPLES

FRONT_POINTS = (BoundaryPoint("a1", "a1"), BoundaryPoint("a2", "a2"),
                BoundaryPoint("broken", "broken"), BoundaryPoint("p", "p", "out", REAR))
REAR_POINTS = (BoundaryPoint("p", "p", "in", FRONT), BoundaryPoint("b1", "b1"),
               BoundaryPoint("b2", "b2"))
FRONT_ROWS = (("feed-A", "a1", "hub-private", 0, 5), ("feed-B", "a2", "hub-private", 0, 4),
              ("fast", "hub-private", "p", 5, 10), ("late-1", "hub-private", "p", 5, 20),
              ("early-stop", "broken", "turn-private", 0, 12),
              ("early-departure", "turn-private", "p", 11, 20))
EXTRA_ROW = ("late-2", "hub-private", "p", 5, 20)
REAR_ROWS = (("lab-A", "p", "b1", 20, 25), ("lab-B", "p", "b2", 20, 26))
STAGGERED_ROWS = (("lab-A", "p", "b1", 11, 25), ("lab-B", "p", "b2", 21, 26))
BROKEN = SampleRequest("broken-sample", "broken", "b1")


def _stage(db, enter, station, label, points, rows):
    key = create_draft(db, station, BATCH, label).key
    nodes = {point.node for point in points}
    nodes.update(node for _, start, end, _, _ in rows for node in (start, end))
    for node in sorted(nodes):
        add_node(db, key, node)
    for row in rows:
        enter(db, key, *row)
    return close_day(db, key, label, points).key


def _prepare(root, enter, publish):
    front_path, rear_path = root / "prep.db", root / "lab.db"
    with closing(open_source(front_path)) as front, closing(open_source(rear_path)) as rear:
        front_key = _stage(front, enter, FRONT, "F1", FRONT_POINTS, FRONT_ROWS)
        old_v1 = publish_pack(front, front_key)
        first = publish(front, front_key)
        successor = open_successor(front, front_key, "F2")
        enter(front, successor.key, *EXTRA_ROW)
        extra_key = close_day(front, successor.key, "F2", FRONT_POINTS).key
        extra = publish(front, extra_key)
        simultaneous = publish(rear, _stage(rear, enter, REAR, "L1", REAR_POINTS, REAR_ROWS))
        staggered = publish(rear, _stage(rear, enter, REAR, "L2", REAR_POINTS, STAGGERED_ROWS))
    with closing(open_source(front_path)) as front:
        assert publish(front, front_key) == first
        assert publish_pack(front, front_key) == old_v1
    front_path.unlink()
    rear_path.unlink()
    return old_v1, first, extra, simultaneous, staggered


def _untimed(db, key, record_id, start, end, depart, arrive):
    return record(db, key, record_id, start, end)


def v1_case(root):
    _, first, extra, rear, _ = _prepare(root, _untimed, publish_pack)
    assert decode_package(first).evidence == decode_package(extra).evidence
    with closing(open_night(root / "night.db")) as night:
        rear_id = import_pack(night, rear)
        for payload in (first, extra):
            front_id = import_pack(night, payload)
            assert issue(night, ReviewRequest(front_id, rear_id, SAMPLES)).interfaces == ("p", "p")
        first_id = import_pack(night, first)
        assert issue(night, ReviewRequest(first_id, rear_id, (BROKEN,))).interfaces == ("p",)
    print("V1 空间核验：两份前站件资格相同，均签发 p,p，逆时序通路也具有空间资格。")


def _reject(call, error, message=None):
    try:
        call()
    except error as exc:
        if message is not None and str(exc) != message:
            raise
        return str(exc)
    raise AssertionError(f"expected {message or error.__name__}")


def _counts(db):
    return tuple(db.execute(query).fetchone()[0] for query in (
        "SELECT COUNT(*) FROM issued_results", "SELECT COUNT(*) FROM receipts"))


def timed_case(root):
    from intake import record_timed
    from publish import publish_timed_pack
    from results import issue_timed
    from review import import_timed_pack

    old_v1, *payloads = _prepare(root, record_timed, publish_timed_pack)
    assert all("hub-private" not in payload and "late-1" not in payload for payload in payloads)
    night_path = root / "night.db"
    with closing(open_night(night_path)) as night:
        v1_id = import_pack(night, old_v1)
        first, extra, rear, staggered = (import_timed_pack(night, p.encode("utf-8")) for p in payloads)
        assert first != v1_id and first != extra
        assert import_timed_pack(night, payloads[0]) == first
        singles = []
        for sample in SAMPLES:
            single = issue_timed(night, ReviewRequest(first, rear, (sample,)), 2)
            assert single.interfaces == ("p",) and single.handoffs == ((20, 20),)
            singles.append(single.handoffs[0])
        before = _counts(night)
        refusal = _reject(lambda: issue_timed(night, ReviewRequest(first, rear, SAMPLES), 2),
                          ValueError, "NO_COMMON_EVIDENCE")
        _reject(lambda: issue_timed(night, ReviewRequest(first, rear, (BROKEN,)), 2),
                ValueError, "NO_COMMON_EVIDENCE")
        _reject(lambda: issue_timed(night, ReviewRequest(v1_id, rear, SAMPLES), 2), ValueError)
        assert _counts(night) == before
        print(json.dumps({"single_handoffs": singles, "double_refusal": refusal}))

        simultaneous = issue_timed(night, ReviewRequest(extra, rear, SAMPLES), 0)
        assert simultaneous.interfaces == ("p", "p")
        assert simultaneous.handoffs == ((20, 20), (20, 20))
        print(json.dumps({"parallel_record_handoffs": simultaneous.handoffs}))
        tied = issue_timed(night, ReviewRequest(first, rear, SAMPLES), 10)
        assert tied.handoffs == ((10, 20), (20, 20))
        reverse = issue_timed(night, ReviewRequest(first, staggered, SAMPLES[::-1]), 1)
        assert reverse.samples == SAMPLES[::-1] and reverse.handoffs == ((20, 21), (10, 11))

        request = ReviewRequest(first, staggered, SAMPLES)
        before = _counts(night)
        night.execute("CREATE TEMP TRIGGER fail_timed_receipt BEFORE INSERT ON receipts "
                      "BEGIN SELECT RAISE(ABORT, 'fixture receipt write failed'); END")
        _reject(lambda: issue_timed(night, request, 1), sqlite3.IntegrityError,
                "fixture receipt write failed")
        assert _counts(night) == before
        night.execute("DROP TRIGGER fail_timed_receipt")
        signed = issue_timed(night, request, 1)
        assert signed.interfaces == ("p", "p") and signed.handoffs == ((10, 11), (20, 21))
        assert signed.max_wait == 1 and signed.samples == SAMPLES
        assert tuple(parent.pack_id for parent in signed.parents) == (first, staggered)
        assert signed.receipt.result_id == signed.result_id
        excerpt = export_excerpt(night, signed.result_id)

    with closing(open_night(night_path)) as night:
        assert fetch(night, signed.result_id) == signed
        assert export_excerpt(night, signed.result_id) == excerpt
        body = json.loads(excerpt)
        assert body["max_wait"] == 1
        assert [(row["front_arrive"], row["rear_depart"]) for row in body["samples"]] == [(10, 11), (20, 21)]
        print(excerpt)


def main():
    parser = argparse.ArgumentParser(description="核对样品流转交班")
    parser.add_argument("mode", choices=("v1", "verify", "demo"))
    mode = parser.parse_args().mode
    if mode == "verify":
        from check_project import main as check_v1
        status = check_v1()
        if status:
            raise SystemExit(status)
    elif mode == "demo":
        from demo import main as demo_v1
        demo_v1()
    with TemporaryDirectory(prefix="timed-handoff-") as directory:
        case = v1_case if mode == "v1" else timed_case
        case(Path(directory))


if __name__ == "__main__":
    main()
