"""Dev wrapper: py devtools/altium_dlg.py list|close|shot out.png"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from convi.altium.dialogs import *  # noqa
from convi.altium import dialogs as _d

if __name__ == "__main__":
    sys.argv[0] = _d.__file__
    exec(compile(open(_d.__file__, encoding="utf-8").read(), _d.__file__, "exec"), {"__name__": "__main__"})
