# -*- mode: python ; coding: utf-8 -*-

# ipmg 配置可视化编辑器打包配置（独立 exe，窗口模式）
a = Analysis(
    ['editor/__main__.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['netaddr', 'yaml'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'rich', 'wmi', 'win32com', 'win32api', 'win32clipboard',
        'matplotlib', 'numpy', 'pandas', 'PIL',
        'test', 'pdb', 'doctest',
        'ftplib', 'poplib', 'imaplib', 'nntplib', 'smtplib',
    ],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure, optimize=2)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ipmg-config-editor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
