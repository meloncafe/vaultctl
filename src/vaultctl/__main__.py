"""vaultctl 패키지 진입점."""

# Force import all command modules for PyInstaller
# PyInstaller를 위해 모든 명령어 모듈 강제 import
import vaultctl.commands.admin
import vaultctl.commands.compose
import vaultctl.commands.extended
import vaultctl.commands.repo
import vaultctl.commands.setup

from vaultctl.cli import app

if __name__ == "__main__":
    app()
