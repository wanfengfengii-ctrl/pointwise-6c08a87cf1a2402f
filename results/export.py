import json
from dataclasses import asdict

from .archive import fetch


def export_excerpt(db, result_id):
    result = fetch(db, result_id)
    if result.max_wait is not None:
        return json.dumps({
            'result_id': result.result_id,
            'parents': [asdict(parent) for parent in result.parents],
            'max_wait': result.max_wait,
            'samples': [
                {
                    **asdict(sample),
                    'interface': interface,
                    'front_arrive': handoff[0],
                    'rear_depart': handoff[1],
                }
                for sample, interface, handoff in zip(
                    result.samples, result.interfaces, result.handoffs)
            ],
            'receipt': asdict(result.receipt),
        }, sort_keys=True, separators=(',', ':'))
    return json.dumps({
        'result_id': result.result_id,
        'parents': [asdict(parent) for parent in result.parents],
        'samples': [
            {**asdict(sample), 'interface': interface}
            for sample, interface in zip(result.samples, result.interfaces)
        ],
        'receipt': asdict(result.receipt),
    }, sort_keys=True, separators=(',', ':'))
