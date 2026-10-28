# PyInstaller spec — pyinstaller --noconfirm wing_auto_eq.spec
# onedir 방식 (시작 속도와 안정성 때문에 onefile 대신 사용)
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = collect_data_files("_sounddevice_data") + [("config/*.example.json", "config"), ("docs", "docs"),
                                                   ("README.md", ".")]
binaries = collect_dynamic_libs("_sounddevice_data")

a = Analysis(["main.py"], pathex=["."], binaries=binaries, datas=datas,
             hiddenimports=["scipy.signal", "scipy.io.wavfile"],
             excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="WingAutoEQ", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="WingAutoEQ")

# 연결 검증 도구(콘솔)
b = Analysis(["tools/verify_connection.py"], pathex=["."], binaries=binaries, datas=[])
pyzb = PYZ(b.pure)
exeb = EXE(pyzb, b.scripts, [], exclude_binaries=True, name="WingVerify", console=True)
collb = COLLECT(exeb, b.binaries, b.datas, name="WingVerify")
