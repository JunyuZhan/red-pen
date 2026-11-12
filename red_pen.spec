# -*- mode: python ; coding: utf-8 -*-
# 红笔 · 教师单机自用版 PyInstaller 打包配置
# 用法: pyinstaller red_pen.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# RapidOCR（g1 题号锚点）：模型权重在包内 data，需一并打入
_rapid_datas = collect_data_files('rapidocr_onnxruntime')
_rapid_hidden = collect_submodules('rapidocr_onnxruntime')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('frontend', 'frontend')] + _rapid_datas,
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'cv2',  # CLAHE 低对比度增强（缺失时运行时自动回退 autocontrast）
    ] + _rapid_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RedPen',
    icon='assets/icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
