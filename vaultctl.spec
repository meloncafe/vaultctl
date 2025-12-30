# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for vaultctl."""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# 소스 경로
src_path = Path("src")
templates_path = src_path / "vaultctl" / "templates"

# Collect all vaultctl submodules from installed package
# 설치된 패키지에서 모든 vaultctl 하위 모듈 수집
vaultctl_imports = collect_submodules('vaultctl')
print(f"Collected vaultctl modules: {vaultctl_imports}")

a = Analysis(
    [str(src_path / "vaultctl" / "__main__.py")],
    pathex=[str(src_path)],
    binaries=[],
    datas=[
        # Include Jinja2 templates / Jinja2 템플릿 포함
        (str(templates_path), "vaultctl/templates"),
    ],
    hiddenimports=vaultctl_imports + [
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
