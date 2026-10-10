"""Desktop notifications through ``notify-send`` (silently skipped if unavailable)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

log = logging.getLogger(__name__)


def notify(title: str, body: str, *, urgency: str = "normal") -> None:
    command = shutil.which("notify-send")
    if not command:
        return
    env = dict(os.environ)
    # Timers and cron jobs may run without the session bus in their environment.
    bus = f"/run/user/{os.getuid()}/bus"
    if "DBUS_SESSION_BUS_ADDRESS" not in env and os.path.exists(bus):
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    try:
        subprocess.run(
            [command, "--app-name=hdmirror", f"--urgency={urgency}", "--icon=folder-download", title, body],
            env=env,
            timeout=10,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as err:
        log.debug("notification failed: %s", err)
