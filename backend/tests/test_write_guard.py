"""Write-command guardrail for ssh_exec / telnet_exec.

Verifies the classification + policy (pure logic, no network): a command marked
`write` is blocked unless the tool sets allow_write.
"""
from app.services.tools.executor import _resolve_command, _write_blocked

COMMANDS = {
    "commands": {
        "show_version": {"command": "show version"},
        "reboot": {"command": "reboot", "write": True},
        "set_descr": {"command": "set port {port} description {text}", "params": ["port", "text"], "write": True},
    }
}


def test_read_command_not_write():
    r = _resolve_command(COMMANDS, {"command_name": "show_version"})
    assert r.is_write is False
    assert r.text == "show version"
    assert not _write_blocked(COMMANDS, r)


def test_write_command_blocked_by_default():
    r = _resolve_command(COMMANDS, {"command_name": "reboot"})
    assert r.is_write is True
    assert _write_blocked(COMMANDS, r) is True


def test_write_command_allowed_with_optin():
    runtime = {**COMMANDS, "allow_write": True}
    r = _resolve_command(runtime, {"command_name": "reboot"})
    assert r.is_write is True
    assert _write_blocked(runtime, r) is False


def test_write_command_with_params_still_substitutes():
    r = _resolve_command(COMMANDS, {"command_name": "set_descr", "params": {"port": "1", "text": "uplink"}})
    assert r.is_write is True
    assert r.text == "set port 1 description uplink"
