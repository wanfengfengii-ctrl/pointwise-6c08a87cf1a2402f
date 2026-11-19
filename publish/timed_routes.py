from collections import defaultdict

from intake.timed_transfers import TimedTransfer


def collect_timed_witnesses(frozen):
    """Enumerate every timed walk between public boundary points.

    Only timed records can form timed provenance. Consecutive records must
    connect in time: the previous arrival is not later than the next
    departure. Each returned entry maps a directed public relation and its
    front/rear station-boundary time to the record sets that realize it.
    """
    timed = tuple(item for item in frozen.transfers if isinstance(item, TimedTransfer))
    outgoing = defaultdict(list)
    for transfer in timed:
        outgoing[transfer.start].append(transfer)

    public_at_node = defaultdict(list)
    for point in frozen.boundaries:
        public_at_node[point.node].append(point.public_id)

    # witnesses[relation][(front_time, rear_time)] = list of record id sets
    witnesses = defaultdict(lambda: defaultdict(list))
    for origin in frozen.boundaries:
        # State: node, occupied record ids, first departure, last arrival.
        pending = [(origin.node, frozenset(), None, None)]
        while pending:
            node, records, first_depart, last_arrive = pending.pop()
            if records:
                for target in public_at_node.get(node, ()):
                    relation = (origin.public_id, target)
                    bucket = witnesses[relation][(last_arrive, first_depart)]
                    if records not in bucket:
                        bucket.append(records)
            for transfer in reversed(outgoing.get(node, ())):
                if transfer.record_id in records:
                    continue
                if last_arrive is not None and last_arrive > transfer.depart:
                    continue
                next_depart = transfer.depart if first_depart is None else first_depart
                pending.append((
                    transfer.end,
                    records | {transfer.record_id},
                    next_depart,
                    transfer.arrive,
                ))
    return {
        relation: {times: tuple(record_sets) for times, record_sets in by_time.items()}
        for relation, by_time in witnesses.items()
    }
