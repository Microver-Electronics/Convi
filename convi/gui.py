"""Convi GUI entry point.

The wizard is gui_app (CustomTkinter, dark theme).  Without CustomTkinter installed, or with the
environment variable CONVI_GUI=classic, the classic Tk wizard (gui_classic) starts instead.
"""
from __future__ import annotations

import os


def run_gui():
    if os.environ.get("CONVI_GUI", "").lower() != "classic":
        try:
            import customtkinter  # noqa: F401
        except ImportError:
            print("CustomTkinter is not installed (run install.bat); starting the classic wizard.")
        else:
            from .gui_app import run_gui as modern
            return modern()
    from .gui_classic import run_gui as classic
    return classic()
