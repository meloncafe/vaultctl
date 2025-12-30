"""User commands for vaultctl.
사용자 명령어.

Usage:
    vaultctl compose init <n>    # Initialize Docker Compose with Vault
    vaultctl compose up <n>      # Sync secrets and start containers
    vaultctl compose down        # Stop containers
    vaultctl compose restart <n> # Sync secrets and restart
    
    vaultctl run <n> -- cmd      # Run with injected env vars
    vaultctl sh <n>              # Generate shell export
    vaultctl scan                # Scan for hardcoded secrets
    vaultctl redact              # Mask secrets in logs
    vaultctl watch <n> -- cmd    # Auto-restart on secret change
"""

from vaultctl.commands.user import compose, extended

__all__ = ["compose", "extended"]
