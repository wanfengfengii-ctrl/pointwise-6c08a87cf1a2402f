from dataclasses import dataclass
from itertools import product

from publish.timed_package import TimedStationPackage

from .intake import load_any_imported_package
from .requests import ReviewRequest, validate_request
from .seams import match_seam
from .decisions import ParentRef


@dataclass(frozen=True)
class TimedReviewDecision:
    parents: tuple[ParentRef, ParentRef]
    request: ReviewRequest
    interfaces: tuple[str, ...]
    handoffs: tuple[tuple[int, int], ...]
    max_wait: int


def require_max_wait(max_wait):
    if type(max_wait) is not int or max_wait < 0:
        raise ValueError('INVALID_MAX_WAIT')
    return max_wait


def _feasible_handoffs(front, rear, request, binding, max_wait):
    front_relations = tuple(
        (sample.start, port) for sample, port in zip(request.samples, binding)
    )
    rear_relations = tuple(
        (port, sample.end) for sample, port in zip(request.samples, binding)
    )

    if len(request.samples) == 1:
        arrives = front.evidence.single_times(front_relations[0], 'front')
        departs = rear.evidence.single_times(rear_relations[0], 'rear')
        return tuple(
            ((arrive, depart),)
            for arrive in arrives
            for depart in departs
            if 0 <= depart - arrive <= max_wait
        )

    first_front, second_front = front_relations
    first_rear, second_rear = rear_relations
    front_options = front.evidence.pair_times(first_front, second_front, 'front')
    rear_options = rear.evidence.pair_times(first_rear, second_rear, 'rear')
    if not front_options or not rear_options:
        return ()
    feasible = []
    for arrives in front_options:
        for departs in rear_options:
            if all(0 <= depart - arrive <= max_wait
                   for arrive, depart in zip(arrives, departs)):
                feasible.append(tuple(zip(arrives, departs)))
    return tuple(feasible)


def decide_timed(db, request, max_wait):
    require_max_wait(max_wait)
    validate_request(request)
    front = load_any_imported_package(db, request.front_id)
    rear = load_any_imported_package(db, request.rear_id)
    if not isinstance(front, TimedStationPackage) or not isinstance(rear, TimedStationPackage):
        raise ValueError('INVALID_PACKAGE')
    seam = match_seam(front, rear)
    validate_request(request, front, rear)

    best = None
    for binding in product(seam.interfaces, repeat=len(request.samples)):
        handoffs = _feasible_handoffs(front, rear, request, binding, max_wait)
        if not handoffs:
            continue
        choice = min(handoffs)
        if best is None or (binding, choice) < (best[0], best[1]):
            best = (binding, choice)
    if best is None:
        raise ValueError('NO_COMMON_EVIDENCE')
    binding, handoffs = best
    return TimedReviewDecision(
        (ParentRef.from_package(front), ParentRef.from_package(rear)),
        request, binding, handoffs, max_wait,
    )
