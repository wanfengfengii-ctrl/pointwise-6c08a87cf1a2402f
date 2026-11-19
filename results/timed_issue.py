from review.timed_decisions import decide_timed

from .archive import IssuedResult, write_timed_result
from .receipts import write_receipt
from .store import issue_transaction


def issue_timed(db, request, max_wait):
    decision = decide_timed(db, request, max_wait)
    with issue_transaction(db):
        result_id = write_timed_result(db, decision)
        receipt = write_receipt(db, result_id)
    return IssuedResult(
        result_id, decision.parents, decision.request.samples, decision.interfaces,
        receipt, decision.max_wait, decision.handoffs,
    )
