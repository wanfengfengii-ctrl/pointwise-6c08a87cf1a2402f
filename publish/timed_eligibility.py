from dataclasses import dataclass
from itertools import combinations_with_replacement

from .timed_routes import collect_timed_witnesses

Relation = tuple[str, str]
RelationPair = tuple[Relation, Relation]
TimePair = tuple[int, int]


def canonical_pair(first, second):
    return (first, second) if first <= second else (second, first)


def ordered_times(canonical_relations, ordered_relations, times):
    mapping = {relation: index for index, relation in enumerate(canonical_relations)}
    return tuple(times[mapping[relation]] for relation in ordered_relations)


@dataclass(frozen=True)
class TimedSingle:
    front: tuple[int, ...]
    rear: tuple[int, ...]


@dataclass(frozen=True)
class TimedPair:
    front: tuple[TimePair, ...]
    rear: tuple[TimePair, ...]


@dataclass(frozen=True)
class TimedEvidence:
    singles: dict[Relation, TimedSingle]
    pairs: dict[RelationPair, TimedPair]

    def single_times(self, relation, role):
        entry = self.singles.get(relation)
        if entry is None:
            return ()
        return entry.front if role == 'front' else entry.rear

    def pair_times(self, first, second, role):
        entry = self.pairs.get(canonical_pair(first, second))
        if entry is None:
            return ()
        pool = entry.front if role == 'front' else entry.rear
        canonical = canonical_pair(first, second)
        return tuple(ordered_times(canonical, (first, second), times) for times in pool)


def _record_sets(witnesses, relation, role, moment):
    index = 0 if role == 'front' else 1
    return [
        record_set
        for times, record_set in witnesses.get(relation, {}).items()
        if times[index] == moment
    ]


def _disjoint_set(options_left, options_right):
    return any(
        left.isdisjoint(right)
        for bucket_left in options_left
        for left in bucket_left
        for bucket_right in options_right
        for right in bucket_right
    )


def build_timed_eligibility(frozen):
    witnesses = collect_timed_witnesses(frozen)
    relations = tuple(sorted(witnesses))
    singles = {}
    for relation in relations:
        front_moments = sorted({times[0] for times in witnesses[relation]})
        rear_moments = sorted({times[1] for times in witnesses[relation]})
        singles[relation] = TimedSingle(tuple(front_moments), tuple(rear_moments))

    pairs = {}
    for first, second in combinations_with_replacement(relations, 2):
        canonical = (first, second)
        front_options, rear_options = set(), set()
        for first_front in singles[first].front:
            for second_front in singles[second].front:
                if _disjoint_set(
                    _record_sets(witnesses, first, 'front', first_front),
                    _record_sets(witnesses, second, 'front', second_front),
                ):
                    front_options.add((first_front, second_front))
        for first_rear in singles[first].rear:
            for second_rear in singles[second].rear:
                if _disjoint_set(
                    _record_sets(witnesses, first, 'rear', first_rear),
                    _record_sets(witnesses, second, 'rear', second_rear),
                ):
                    rear_options.add((first_rear, second_rear))
        if front_options or rear_options:
            pairs[canonical] = TimedPair(
                tuple(sorted(front_options)), tuple(sorted(rear_options)),
            )
    return TimedEvidence(singles, pairs)
