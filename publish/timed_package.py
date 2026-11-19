import json
from dataclasses import dataclass

from freeze.boundaries import validate_boundary_role
from intake.days import require_identity

from .package import _unique_object
from .timed_eligibility import (
    Relation,
    RelationPair,
    TimedEvidence,
    TimedPair,
    TimedSingle,
    canonical_pair,
)


@dataclass(frozen=True)
class TimedStationPackage:
    pack_id: str
    station: str
    batch: str
    revision: str
    boundaries: tuple
    evidence: TimedEvidence


def _require_second(value):
    if type(value) is not int or value < 0:
        raise ValueError('INVALID_EVIDENCE')
    return value


def _read_relation(entry, public_ids):
    if not isinstance(entry, list) or len(entry) != 2:
        raise ValueError('INVALID_EVIDENCE')
    start, end = entry
    if not isinstance(start, str) or not isinstance(end, str):
        raise ValueError('INVALID_EVIDENCE')
    if start == end or start not in public_ids or end not in public_ids:
        raise ValueError('INVALID_EVIDENCE')
    return start, end


def _read_moments(entry):
    if not isinstance(entry, list):
        raise ValueError('INVALID_EVIDENCE')
    return tuple(sorted({_require_second(value) for value in entry}))


def _read_time_tuple(entry):
    if not isinstance(entry, list) or len(entry) != 2:
        raise ValueError('INVALID_EVIDENCE')
    return _require_second(entry[0]), _require_second(entry[1])


def validate_timed_evidence(value, public_ids):
    if not isinstance(value, dict) or set(value) != {'singles', 'pairs'}:
        raise ValueError('INVALID_EVIDENCE')
    if not isinstance(value['singles'], list) or not isinstance(value['pairs'], list):
        raise ValueError('INVALID_EVIDENCE')

    singles = {}
    for entry in value['singles']:
        if not isinstance(entry, dict) or set(entry) != {'relation', 'front', 'rear'}:
            raise ValueError('INVALID_EVIDENCE')
        relation = _read_relation(entry['relation'], public_ids)
        if relation in singles:
            raise ValueError('INVALID_EVIDENCE')
        singles[relation] = TimedSingle(_read_moments(entry['front']),
                                        _read_moments(entry['rear']))

    pairs = {}
    for entry in value['pairs']:
        if not isinstance(entry, dict) or set(entry) != {'relations', 'front', 'rear'}:
            raise ValueError('INVALID_EVIDENCE')
        if not isinstance(entry['relations'], list) or len(entry['relations']) != 2:
            raise ValueError('INVALID_EVIDENCE')
        first = _read_relation(entry['relations'][0], public_ids)
        second = _read_relation(entry['relations'][1], public_ids)
        if first not in singles or second not in singles:
            raise ValueError('INVALID_EVIDENCE')
        ordered = (first, second)
        canonical = canonical_pair(first, second)
        front, rear = set(), set()
        for pool, role, target in ((entry['front'], 'front', front),
                                   (entry['rear'], 'rear', rear)):
            if not isinstance(pool, list):
                raise ValueError('INVALID_EVIDENCE')
            for row in pool:
                times = _read_time_tuple(row)
                for relation, moment in zip(ordered, times):
                    if moment not in getattr(singles[relation], role):
                        raise ValueError('INVALID_EVIDENCE')
                if canonical != ordered:
                    times = (times[1], times[0])
                target.add(times)
        pair_key = canonical
        if pair_key in pairs:
            raise ValueError('INVALID_EVIDENCE')
        pairs[pair_key] = TimedPair(tuple(sorted(front)), tuple(sorted(rear)))
    return TimedEvidence(singles, pairs)


def decode_timed_package(payload):
    try:
        if isinstance(payload, bytes):
            payload = payload.decode('utf-8')
        if not isinstance(payload, str):
            raise ValueError('INVALID_PACKAGE')
        body = json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('INVALID_PACKAGE') from exc
    fields = {'version', 'id', 'station', 'batch', 'revision', 'boundaries', 'eligibility'}
    if not isinstance(body, dict) or set(body) != fields:
        raise ValueError('INVALID_PACKAGE')
    if type(body['version']) is not int or body['version'] != 2:
        raise ValueError('INVALID_PACKAGE')
    pack_id, station, batch, revision = (
        require_identity(body[field], 'INVALID_PACKAGE')
        for field in ('id', 'station', 'batch', 'revision')
    )
    from .package import _read_boundaries

    by_id = _read_boundaries(body['boundaries'], station)
    return TimedStationPackage(
        pack_id, station, batch, revision,
        tuple(by_id[key] for key in sorted(by_id)),
        validate_timed_evidence(body['eligibility'], by_id.keys()),
    )


def encode_timed_package(package):
    body = {
        'version': 2,
        'id': package.pack_id,
        'station': package.station,
        'batch': package.batch,
        'revision': package.revision,
        'boundaries': [
            {'id': point.public_id, 'direction': point.direction, 'neighbor': point.neighbor}
            for point in sorted(package.boundaries, key=lambda item: item.public_id)
        ],
        'eligibility': {
            'singles': [
                {'relation': list(relation), 'front': list(entry.front), 'rear': list(entry.rear)}
                for relation, entry in sorted(package.evidence.singles.items())
            ],
            'pairs': [
                {
                    'relations': [list(first), list(second)],
                    'front': [list(times) for times in entry.front],
                    'rear': [list(times) for times in entry.rear],
                }
                for (first, second), entry in sorted(package.evidence.pairs.items())
            ],
        },
    }
    return json.dumps(body, sort_keys=True, separators=(',', ':'))
