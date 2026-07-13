"""
__init__.py — Package security pour Local_Agent.
Blindage shell: blacklist (couche 1) + plumbum (couche 2) + psutil (couche 3).
Phase I: zombie_killer (hooks de cycle de vie — atexit + signaux + crash backup).
"""
from .command_blacklist import is_blacklisted, check as blacklist_check
from .plumbum_shell import run as shell_run, ShellError
from .psutil_guard import check_resources, get_system_load, ResourceError, guard
from .zombie_killer import install_hooks, register_child, unregister_child, validate_command
from .firejail_sandbox import run_sandboxed, validate_sandbox, is_available as firejail_available

__all__ = [
    "is_blacklisted",
    "blacklist_check",
    "shell_run",
    "ShellError",
    "check_resources",
    "get_system_load",
    "ResourceError",
    "guard",
    "install_hooks",
    "register_child",
    "unregister_child",
    "validate_command",
    "run_sandboxed",
    "validate_sandbox",
    "firejail_available",
]