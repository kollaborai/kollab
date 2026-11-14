"""Every limit of the Code, Git and Terminal views has a switch (#987, docs/specs/code-shell-settings.md).

Resolution (agent.json ``code`` > config > default), the gates in ``code_service.call`` and what each terminal setting
changes in a real shell. A setting with the wrong type must fall back to the default, never close the door.
"""

import asyncio
import json

import pytest

from kollabor import code_service
from kollabor.code_service import terminal
from kollabor.code_service.settings import DEFAULTS, CodeSettings, read_agent_code, resolve

from .test_terminal import Link, _shells  # noqa: F401  (the autouse fixture ends every shell)

pytestmark = pytest.mark.asyncio


def _get(values):
    return lambda path, default=None: values.get(path, default)


# ---------------------------------------------------------------------------------------------- resolution


def test_no_config_and_no_agent_gives_the_defaults():
    assert resolve(None, None) == DEFAULTS
    assert resolve(_get({}), {}) == DEFAULTS


def test_a_config_value_beats_the_default_and_an_agent_value_beats_the_config():
    config = _get({"kollabor.code.terminal.max_shells": 3, "kollabor.code.read_only": True})

    assert resolve(config).max_shells == 3
    assert resolve(config).read_only is True
    mine = resolve(config, {"terminal": {"max_shells": 5}, "read_only": False})
    assert mine.max_shells == 5 and mine.read_only is False


def test_an_agents_dotted_and_nested_keys_both_work():
    assert resolve(None, {"terminal.shell": "/bin/sh"}).shell == "/bin/sh"
    assert resolve(None, {"terminal": {"shell": "/bin/sh"}}).shell == "/bin/sh"


def test_the_wrong_type_falls_back_to_the_next_tier_not_to_a_closed_door():
    config = _get({"kollabor.code.terminal.max_shells": 3, "kollabor.code.enabled": "no"})

    mine = resolve(config, {"terminal": {"max_shells": "many"}, "enabled": 0})

    assert mine.max_shells == 3  # the agent's value was unusable: the config's applies
    assert mine.enabled is True  # nothing usable anywhere: the default, not "off"


def test_numbers_are_clamped_and_booleans_are_not_numbers():
    assert resolve(None, {"terminal.max_shells": 10_000}).max_shells == 64
    assert resolve(None, {"terminal.max_shells": 0}).max_shells == 1
    assert resolve(None, {"terminal.max_shells": True}).max_shells == DEFAULTS.max_shells
    assert resolve(None, {"terminal.idle_reap_minutes": 0}).idle_reap_minutes == 0
    assert resolve(None, {"terminal.idle_reap_minutes": float("nan")}).idle_reap_minutes == 10


def test_env_lists_keep_only_real_names_and_env_set_keeps_text_values():
    mine = resolve(
        None,
        {
            "terminal.env_allow": ["GLM_TOKEN", "not a name", 7, "GLM_TOKEN"],
            "terminal.env_set": {"EDITOR": "vim", "9bad": "x", "N": 3},
        },
    )

    assert mine.env_allow == ("GLM_TOKEN",)
    assert dict(mine.env_set) == {"EDITOR": "vim", "N": "3"}


def test_a_failing_config_get_means_defaults():
    def broken(path, default=None):
        raise RuntimeError("config is gone")

    assert resolve(broken, None) == DEFAULTS


def test_read_agent_code_reads_the_code_object_and_ignores_trouble(tmp_path):
    assert read_agent_code(None) is None
    assert read_agent_code(tmp_path) is None  # no agent.json
    (tmp_path / "agent.json").write_text("{nope")
    assert read_agent_code(tmp_path) is None
    (tmp_path / "agent.json").write_text(json.dumps({"code": ["not", "an", "object"]}))
    assert read_agent_code(tmp_path) is None
    (tmp_path / "agent.json").write_text(json.dumps({"code": {"read_only": True}}))
    assert read_agent_code(tmp_path) == {"read_only": True}


# ---------------------------------------------------------------------------------------------- the gates


async def _call(op, params, tmp_path, **settings):
    return await code_service.call(op, params, root=str(tmp_path), settings=CodeSettings(**settings))


async def test_enabled_false_refuses_everything_but_still_answers_what_is_set(tmp_path):
    for op, params in (("fs.tree", {}), ("git", {"action": "status"}), ("terminal.poll", {"link": "x"})):
        answer = await _call(op, params, tmp_path, enabled=False)
        assert answer["status"] == 404
        assert answer["body"]["error"]["code"] == "code_disabled"
        assert answer["body"]["error"]["setting"] == "kollabor.code.enabled"
    report = await _call("settings", {}, tmp_path, enabled=False)
    assert report["status"] == 200 and report["body"]["data"]["enabled"] is False


async def test_read_only_refuses_file_writes_but_not_reads_and_names_the_setting(tmp_path):
    (tmp_path / "a.txt").write_text("hi")

    read = await _call("fs.file", {"path": "a.txt"}, tmp_path, read_only=True)
    write = await _call("fs.write_file", {"path": "a.txt", "content": "bye"}, tmp_path, read_only=True)

    assert read["status"] == 200
    assert write["status"] == 403
    assert "kollabor.code.read_only" in write["body"]["error"]["message"]
    assert (tmp_path / "a.txt").read_text() == "hi"
    for op in ("create", "mkdir", "rename", "delete"):
        assert (await _call(f"fs.{op}", {"path": "b"}, tmp_path, read_only=True))["status"] == 403


async def test_writes_work_when_read_only_is_off_which_is_the_default(tmp_path):
    answer = await _call("fs.write_file", {"path": "a.txt", "content": "x"}, tmp_path)

    assert answer["status"] == 200
    assert (tmp_path / "a.txt").read_text() == "x"


async def test_git_read_only_refuses_git_writes_and_allows_git_reads(tmp_path):
    write = await _call("git", {"action": "commit", "message": "m"}, tmp_path, git_read_only=True)
    also = await _call("git", {"action": "stage_all"}, tmp_path, read_only=True)
    read = await _call("git", {"action": "status"}, tmp_path, git_read_only=True)

    assert write["status"] == 403 and write["body"]["error"]["code"] == "git_read_only"
    assert also["status"] == 403 and also["body"]["error"]["code"] == "read_only"
    assert read["status"] != 403  # a non-repo answers its own error, not a settings refusal


async def test_terminal_disabled_refuses_a_new_shell_and_names_the_setting(tmp_path):
    link = Link(tmp_path, CodeSettings(terminal_enabled=False))

    params = {"link": link.id, "raw": json.dumps({"type": "open"})}
    answer = await code_service.call("terminal.message", params, root=link.cwd, settings=link.settings)

    assert answer["status"] == 403
    assert answer["body"]["error"]["setting"] == "kollabor.code.terminal.enabled"


async def test_the_settings_op_reports_values_and_env_set_shows_names_only(tmp_path):
    answer = await _call("settings", {}, tmp_path, env_set=(("API_URL", "http://secret-host"),), max_shells=5)

    data = answer["body"]["data"]
    assert data["max_shells"] == 5
    assert data["env_set"] == ["API_URL"]
    assert "secret-host" not in json.dumps(answer)


# ---------------------------------------------------------------------------------------------- env filters


def test_env_deny_beats_env_allow_and_allow_beats_the_secret_filter(monkeypatch):
    monkeypatch.setenv("GLM_TOKEN", "1")
    monkeypatch.setenv("RUNPOD_API_KEY", "2")
    monkeypatch.setenv("KOLLAB_X", "3")

    env = terminal._shell_env(CodeSettings(env_allow=("GLM_TOKEN", "KOLLAB_X"), env_deny=("KOLLAB_X",)))

    assert env["GLM_TOKEN"] == "1"  # allowed by name although it carries TOKEN
    assert "RUNPOD_API_KEY" not in env  # still filtered
    assert "KOLLAB_X" not in env  # deny wins


def test_pass_kollab_env_lets_kollab_names_through_and_names_compare_without_case(monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "1")
    monkeypatch.setenv("Other_Token", "2")

    assert terminal._shell_env(CodeSettings(pass_kollab_env=True))["KOLLAB_X"] == "1"
    assert terminal._shell_env(CodeSettings(env_allow=("OTHER_TOKEN",)))["Other_Token"] == "2"


def test_env_set_lays_values_over_the_filtered_environment_and_can_replace_term(monkeypatch):
    monkeypatch.setenv("EDITOR", "nano")

    env = terminal._shell_env(CodeSettings(env_set=(("EDITOR", "vim"), ("TERM", "xterm"))))

    assert env["EDITOR"] == "vim" and env["TERM"] == "xterm"


def test_env_report_lists_names_by_reason_and_never_a_value(monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "kollab-value")
    monkeypatch.setenv("MY_TOKEN", "token-value")
    monkeypatch.setenv("NOISE", "n")

    report = terminal.env_report(CodeSettings(env_deny=("NOISE",)))

    assert "MY_TOKEN" in report["hidden"]["secret"]
    assert "KOLLAB_X" in report["hidden"]["kollab"]
    assert report["hidden"]["denied"] == ["NOISE"]
    assert report["count"] == sum(len(names) for names in report["hidden"].values())
    assert "token-value" not in json.dumps(report) and "kollab-value" not in json.dumps(report)


async def test_env_report_is_a_terminal_op_the_ui_can_call(tmp_path):
    answer = await _call("terminal.env_report", {}, tmp_path)

    assert answer["status"] == 200 and "hidden" in answer["body"]["data"]


# ---------------------------------------------------------------------------------------------- shell settings


async def test_the_shell_gets_the_variables_env_allow_and_env_set_give_it(tmp_path, monkeypatch):
    monkeypatch.setenv("GLM_TOKEN", "glm-secret-1")
    link = Link(tmp_path, CodeSettings(env_allow=("GLM_TOKEN",), env_set=(("KOLLAB_NOTE", "from-settings"),)))
    await link.open()
    await link.send(type="input", data="echo got-$GLM_TOKEN-end; echo set-$KOLLAB_NOTE-end\n")

    output = await link.until("set-from-settings-end")

    assert "got-glm-secret-1-end" in output


async def test_start_command_runs_in_every_new_shell(tmp_path):
    link = Link(tmp_path, CodeSettings(start_command="echo started-''by-setting"))
    await link.open()

    assert "started-by-setting" in await link.until("started-by-setting")


async def test_max_shells_is_the_setting_not_a_constant(tmp_path):
    settings = CodeSettings(max_shells=1)
    first = Link(tmp_path, settings)
    await first.open()
    second = Link(tmp_path, settings)

    refused = await second.send(type="open")

    assert refused[0]["type"] == "error"
    assert "kollabor.code.terminal.max_shells" in refused[0]["message"]


async def test_read_only_terminal_takes_no_input_and_says_which_setting(tmp_path):
    link = Link(tmp_path, CodeSettings(read_only=True))
    await link.open()

    answer = await link.send(type="input", data="echo no\n")

    assert answer[0]["type"] == "error" and "kollabor.code.read_only" in answer[0]["message"]


async def test_login_false_starts_the_shell_without_dash_l(tmp_path):
    link = Link(tmp_path, CodeSettings(shell="/bin/sh", login=False))
    await link.open()
    await link.send(type="input", data="echo args-[$0]-end\n")

    assert "args-[/bin/sh]-end" in await link.until("args-[/bin/sh]-end")


async def test_a_configured_shell_that_does_not_exist_falls_back_to_a_real_one(tmp_path):
    assert terminal._login_shell("/no/such/shell") == terminal._login_shell("")


async def test_scrollback_and_idle_reap_follow_the_settings(tmp_path):
    session = terminal._spawn(str(tmp_path), 80, 24, CodeSettings(scrollback_kb=16, idle_reap_minutes=0))
    try:
        assert session._ring_limit == 16 * 1024
        assert session._reap_seconds == 0
        session.schedule_reap()
        assert session._reap_handle is None  # 0 minutes = never reaped
    finally:
        await session.destroy()


# ---------------------------------------------------------------------------------------------- review fixes


def test_infinity_in_a_config_file_never_raises_it_falls_back():
    assert resolve(None, {"files": {"max_file_kb": float("inf")}}).max_file_kb == DEFAULTS.max_file_kb
    assert resolve(_get({"kollabor.code.files.max_file_kb": float("-inf")})).max_file_kb == DEFAULTS.max_file_kb


def test_a_name_with_a_trailing_newline_is_not_an_env_name():
    assert resolve(None, {"terminal.env_allow": ["GLM_TOKEN\n"]}).env_allow == ()
    assert resolve(None, {"terminal.env_set": {"EDITOR\n": "vim"}}).env_set == ()


def test_extra_roots_expand_home_and_drop_relative_entries():
    mine = resolve(None, {"files.extra_roots": ["~/proj", "relative/dir", "/srv/data"]})

    assert mine.extra_roots[0].endswith("/proj") and not mine.extra_roots[0].startswith("~")
    assert "relative/dir" not in mine.extra_roots
    assert "/srv/data" in mine.extra_roots


async def test_start_command_does_not_run_when_read_only_is_on(tmp_path):
    marker = tmp_path / "ran.txt"
    link = Link(tmp_path, CodeSettings(read_only=True, start_command=f"touch {marker}"))
    await link.open()
    await asyncio.sleep(1.0)

    assert not marker.exists()


async def test_detach_still_works_when_the_views_are_turned_off(tmp_path):
    answer = await _call("terminal.detach", {"link": "x"}, tmp_path, enabled=False)

    assert answer["status"] != 404 or answer["body"]["error"]["code"] != "code_disabled"


async def test_a_bare_shell_name_is_not_resolved_in_the_project_folder(tmp_path, monkeypatch):
    fake = tmp_path / "zsh"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.chdir(tmp_path)

    assert terminal._login_shell("zsh") != "zsh"
    assert terminal._login_shell("./zsh") != "./zsh"


async def test_a_directory_is_not_a_shell(tmp_path):
    assert terminal._login_shell(str(tmp_path)) != str(tmp_path)
