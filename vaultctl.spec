# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for vaultctl."""

import sys
from pathlib import Path

block_cipher = None

# 소스 경로
src_path = Path("src")
templates_path = src_path / "vaultctl" / "templates"

# Manually collect all vaultctl modules from src/
# src/에서 모든 vaultctl 모듈을 수동으로 수집
def collect_vaultctl_modules():
    """Scan src/vaultctl for all Python modules."""
    modules = []
    vaultctl_path = src_path / "vaultctl"
    
    for py_file in vaultctl_path.rglob("*.py"):
        # Convert path to module name
        # e.g., src/vaultctl/commands/admin.py -> vaultctl.commands.admin
        rel_path = py_file.relative_to(src_path)
        parts = list(rel_path.parts)
        
        # Remove .py extension
        parts[-1] = parts[-1][:-3]
        
        # Skip __pycache__ and __init__
        if "__pycache__" in parts:
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        
        if parts:
            module_name = ".".join(parts)
            modules.append(module_name)
    
    return modules

vaultctl_modules = collect_vaultctl_modules()
print(f"Collected vaultctl modules: {vaultctl_modules}")

a = Analysis(
    [str(src_path / "vaultctl" / "__main__.py")],
    pathex=[str(src_path)],
    binaries=[],
    datas=[
        # Include Jinja2 templates / Jinja2 템플릿 포함
        (str(templates_path), "vaultctl/templates"),
    ],
    hiddenimports=vaultctl_modules + [
        # 외부 의존성
        "typer",
        "rich",
        "httpx",
        "pydantic",
        "pydantic_settings",
        "dotenv",
        "keyring",
        "jinja2",
        "ruamel.yaml",
        # typer 관련
        "typer.core",
        "typer.main",
        "click",
        "click.core",
        # rich 관련
        "rich.console",
        "rich.table",
        "rich.panel",
        "rich.progress",
        "rich.prompt",
        # pydantic 관련
        "pydantic.fields",
        "pydantic_settings.sources",
        # jinja2 관련
        "jinja2.ext",
        "jinja2.loaders",
        "markupsafe",
        # ruamel.yaml 관련
        "ruamel.yaml.main",
        "ruamel.yaml.comments",
        "ruamel.yaml.clib",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "cv2",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="vaultctl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
