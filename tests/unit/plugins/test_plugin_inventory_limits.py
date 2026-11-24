"""Inventory normalizers stop reading collections at their published bounds."""

import pytest

from kollabor_plugins.registry import (
    _normalize_plugin_entry,
    _normalize_service_entry,
    _normalize_setting,
    _service_secrets,
)


class _BoundedList(list):
    def __init__(self, values, limit):
        super().__init__(values)
        self.limit = limit
        self.reads = 0

    def _read(self):
        self.reads += 1
        if self.reads > self.limit:
            raise AssertionError(f"read more than {self.limit} collection items")

    def __iter__(self):
        for index in range(list.__len__(self)):
            self._read()
            yield list.__getitem__(self, index)

    def __getitem__(self, key):
        value = list.__getitem__(self, key)
        if isinstance(key, slice):
            for _ in value:
                self._read()
        else:
            self._read()
        return value


class _BoundedDict(dict):
    def __init__(self, values, limit):
        super().__init__(values)
        self.limit = limit
        self.reads = 0

    def items(self):
        for index, item in enumerate(dict.items(self)):
            self.reads += 1
            if self.reads > self.limit:
                raise AssertionError(f"read more than {self.limit} mapping items")
            yield item


def _plugin_inventory(field, values):
    entry = _normalize_plugin_entry({"id": "example-plugin", field: values})
    assert entry is not None
    return entry[field]


@pytest.mark.parametrize(
    ("field", "limit", "make_values", "normalize"),
    [
        (
            "dropdown options",
            256,
            lambda count: [f"choice-{index}" for index in range(count)],
            lambda values: _normalize_setting(
                {"type": "dropdown", "path": "example.mode", "label": "Mode", "options": values}
            )["options"],
        ),
        (
            "uses",
            256,
            lambda count: [f"custom:cap-{index}" for index in range(count)],
            lambda values: _plugin_inventory("uses", values),
        ),
        (
            "granted capabilities",
            256,
            lambda count: [f"custom:cap-{index}" for index in range(count)],
            lambda values: _plugin_inventory("granted", values),
        ),
        (
            "service inventory secrets",
            64,
            lambda count: [f"ENV_{index}" for index in range(count)],
            lambda values: _normalize_service_entry({"name": "worker", "secrets": values})["secrets"],
        ),
        (
            "service declaration secrets",
            64,
            lambda count: [f"ENV_{index}" for index in range(count)],
            lambda values: _service_secrets({"secrets": values}),
        ),
    ],
    ids=["options", "uses", "grants", "inventory-secrets", "declared-secrets"],
)
def test_inventory_list_normalizers_stop_at_their_bound(field, limit, make_values, normalize):
    values = _BoundedList(make_values(limit + 1000), limit)

    result = normalize(values)

    assert len(result) == limit, field
    assert values.reads == limit, field


def test_inventory_option_labels_stop_at_their_bound():
    limit = 256
    labels = _BoundedDict(
        {f"choice-{index}": f"Label {index}" for index in range(limit + 1000)},
        limit,
    )

    result = _normalize_setting(
        {"type": "dropdown", "path": "example.mode", "label": "Mode", "option_labels": labels}
    )

    assert result is not None
    assert len(result["option_labels"]) == limit
    assert labels.reads == limit


def test_plugin_inventory_reports_service_use_and_grant_omissions():
    entry = _normalize_plugin_entry(
        {
            "id": "example-plugin",
            "services": [
                {"name": f"worker-{index}", "secrets": [f"ENV_{index}_{secret}" for secret in range(65)]}
                for index in range(129)
            ],
            "uses": [f"custom:cap-{index}" for index in range(257)],
            "granted": [f"custom:cap-{index}" for index in range(257)],
        }
    )

    assert entry is not None
    assert len(entry["services"]) == 128
    assert entry["services"][0]["secrets_omitted"] == 1
    assert entry["omitted"] == {"services": 1, "uses": 1, "granted": 1}


@pytest.mark.parametrize("value", ["run:codex\u200b", "net:github.com\u202eevil.test"])
def test_inventory_text_rejects_invisible_format_controls(value):
    from kollabor_plugins.registry import _bounded_inventory_text

    assert _bounded_inventory_text(value) is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("web/dist/index.js", "web/dist/index.js"),
        ("./web/dist/index.js", "web/dist/index.js"),
        ("web/dist/../secret.js", None),
    ],
)
def test_plugin_web_entry_normalizes_manifest_relative_paths(path, expected):
    entry = _normalize_plugin_entry(
        {
            "id": "example-plugin",
            "web": {"entry": path, "hash": "a" * 64},
        }
    )

    assert entry is not None
    assert (entry["web"]["entry"] if entry["web"] else None) == expected
