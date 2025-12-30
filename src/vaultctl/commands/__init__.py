"""vaultctl commands package.
vaultctl 명령어 패키지.

Structure:
    commands/
    ├── __init__.py          # This file
    ├── admin/               # Admin commands (vaultctl admin ...)
    │   ├── __init__.py      # CLI integration
    │   ├── secrets.py       # list, get, put, delete, import, export
    │   ├── token.py         # token status, token renew
    │   ├── credentials.py   # credentials command
    │   ├── setup.py         # setup vault, apt-server, apt-client
    │   ├── apt_setup.py     # APT setup helpers
    │   └── repo.py          # APT repo management
    ├── user/                # User commands
    │   ├── __init__.py
    │   ├── compose.py       # Docker Compose integration
    │   └── extended.py      # run, sh, scan, redact, watch
    ├── setup.py             # User setup commands (init, systemd, etc.)
    ├── compose.py           # Legacy - redirects to user/compose.py
    ├── extended.py          # Legacy - redirects to user/extended.py
    └── repo.py              # Legacy - redirects to admin/repo.py
"""

from vaultctl.commands.admin import app as admin_app
from vaultctl.commands.user import compose, extended
from vaultctl.commands import setup

__all__ = [
    "admin_app",
    "compose",
    "extended",
    "setup",
]
