#!/usr/bin/env python3
"""Generate throwaway local credentials in OpenBao agent-compatible filenames."""

import os
import secrets
from pathlib import Path

os.umask(0o077)
directory = Path(".secrets")
directory.mkdir(mode=0o700, exist_ok=True)
for name in ("admin_password", "monitor_password", "exporter_password"):
    path = directory / name
    if not path.exists():
        with path.open("x") as output:
            output.write(secrets.token_urlsafe(32) + "\n")
        # Compose bind-mounted files must be readable by differing container UIDs.
        # Parent directory remains 0700. Production uses agent ownership, not this helper.
        path.chmod(0o444)
print("Local dev secret files ready; existing credentials preserved.")
