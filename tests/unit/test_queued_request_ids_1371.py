"""A queued input's request id reaches the batch that answers it (#1371).

batch_request_ids returns the ids of queued inputs together with those of
tagged inputs, so the turn that answers the batch names every request it
answers.
"""

from kollabor_agent.queue_processor import QueuedInput, TaggedInput, batch_request_ids


def test_queued_input_has_no_request_ids_by_default():
    assert QueuedInput("spoken", {}).request_ids == ()


def test_batch_request_ids_includes_queued_inputs():
    batch = [
        QueuedInput("a", {}, request_ids=("r1",)),
        TaggedInput("b", ("r2",)),
        "typed",
    ]
    assert batch_request_ids(batch) == ("r1", "r2")


def test_two_default_queued_inputs_compare_equal():
    assert QueuedInput("spoken", {}) == QueuedInput("spoken", {})
