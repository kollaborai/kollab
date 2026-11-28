"""Regression coverage for unit-test flows that must work without DNS/network."""

import importlib
import socket

import dns.asyncresolver
import dns.resolver
import pytest


def _network_unavailable(*_args, **_kwargs):
    raise OSError("network access is disabled in this unit test")


def _resolver_unavailable(*_args, **_kwargs):
    raise dns.resolver.NoResolverConfiguration("no nameservers in offline test")


def _disable_network(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _network_unavailable)
    monkeypatch.setattr(socket, "create_connection", _network_unavailable)
    monkeypatch.setattr(dns.resolver, "Resolver", _resolver_unavailable)
    monkeypatch.setattr(dns.asyncresolver, "Resolver", _resolver_unavailable)


@pytest.mark.asyncio
async def test_network_name_flows_pass_without_dns(tmp_path, monkeypatch):
    _disable_network(monkeypatch)
    network_name = importlib.import_module("tests.unit.test_network_name")
    scenarios = (
        network_name.test_the_first_device_names_the_network_after_itself,
        network_name.test_a_renamed_device_names_the_network_after_its_current_name,
        network_name.test_the_name_is_chosen_once_and_survives_a_rename_and_a_reconnect,
        network_name.test_a_device_that_was_invited_does_not_name_the_network,
        network_name.test_the_client_keeps_a_name_the_bridge_saved_when_it_saves_its_own_copy,
        network_name.test_rotate_keeps_the_name_and_leave_forgets_it,
    )

    for index, scenario in enumerate(scenarios):
        scenario_path = tmp_path / str(index)
        scenario_path.mkdir()
        await scenario(scenario_path, monkeypatch)


def test_real_source_archive_exclusions_work_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("UV_OFFLINE", "1")
    packaging = importlib.import_module("tests.unit.test_webui_packaging_hook")
    packaging.test_real_source_archive_excludes_frontend_build_metadata(tmp_path)
