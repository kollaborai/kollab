"""The join floor on the primary's side: what it names in the join decision.

The joining device's half (keeping it, refusing a replay below it) is in
test_config_sync.py; the section is docs/specs/agent-network-simple-flow.md 9.
"""

import time
from types import SimpleNamespace

from nacl.signing import SigningKey

from plugins.hub.config_sync import ConfigSyncError
from plugins.hub.config_sync_service import ConfigSyncService
from plugins.hub.enrollment_client import EnrollmentIssuer

DIGEST = "d" * 64


def make_service(tmp_path):
    return ConfigSyncService(
        key=SigningKey.generate(),
        transport=None,
        online=dict,
        recipients=list,
        primary=lambda: "",
        device_name=lambda: "mac",
        peer_name=lambda key: "peer",
        root=tmp_path,
    )


def stamp(service, digest=DIGEST):
    service._stamp(SimpleNamespace(digest=digest))


def test_a_primary_that_has_not_stamped_floors_at_the_clock_with_no_digest(tmp_path):
    before = int(time.time() * 1000)
    revision, digest = make_service(tmp_path).join_floor()

    assert before <= revision <= int(time.time() * 1000) and digest == ""


def test_a_stamped_primary_floors_at_the_revision_its_next_bundle_repeats(tmp_path):
    service = make_service(tmp_path)
    stamp(service)
    floor = service.join_floor()

    stamp(service)  # nothing changed: the next bundle carries the same revision
    assert floor == (service._revision, DIGEST) == service.join_floor()

    stamp(service, "e" * 64)  # a change restamps above the floor, never below
    assert service._revision > floor[0]


def test_the_floor_is_above_what_a_device_that_said_stale_holds(tmp_path):
    service = make_service(tmp_path)
    stamp(service)
    far_ahead = service._revision + 10**6
    try:
        service._expect({"error": "stale", "revision": far_ahead})
    except ConfigSyncError:
        pass

    revision, digest = service.join_floor()

    assert revision > far_ahead and digest == ""  # it will restamp before it sends


def test_the_decision_carries_the_floor_as_two_optional_fields(tmp_path):
    service = make_service(tmp_path)
    stamp(service)
    issuer = EnrollmentIssuer(SimpleNamespace(config_sync=service))

    assert issuer._config_floor_field() == {
        "config_revision": service._revision,
        "config_digest": DIGEST,
    }


def test_an_unstamped_primary_names_a_revision_but_no_digest(tmp_path):
    issuer = EnrollmentIssuer(SimpleNamespace(config_sync=make_service(tmp_path)))

    fields = issuer._config_floor_field()

    assert set(fields) == {"config_revision"} and fields["config_revision"] > 0


def test_no_field_when_sync_is_not_running_or_cannot_answer():
    def breaks():
        raise RuntimeError("no")

    for bridge in (
        SimpleNamespace(),
        SimpleNamespace(config_sync=None),
        SimpleNamespace(config_sync=SimpleNamespace(join_floor=breaks)),
        SimpleNamespace(config_sync=SimpleNamespace(join_floor=lambda: ("1", ""))),
        SimpleNamespace(config_sync=SimpleNamespace(join_floor=lambda: (True, ""))),
        SimpleNamespace(config_sync=SimpleNamespace(join_floor=lambda: (0, ""))),
    ):
        assert EnrollmentIssuer(bridge)._config_floor_field() == {}
