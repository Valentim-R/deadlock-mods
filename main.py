#!/usr/bin/env python3
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if getattr(sys, "frozen", False) and sys.platform == "win32":
    # .exe do Windows não tem console: o que seria impresso vai pra app.log (a execução anterior fica em app.prev.log).
    import faulthandler
    log = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "deadlock-mods" / "app.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists():
        log.replace(log.with_name("app.prev.log"))
    sys.stdout = sys.stderr = open(log, "w", encoding="utf-8", buffering=1)
    faulthandler.enable(sys.stderr)   # crash no driver de vídeo também deixa rastro

from dlmods.ui import main

main()
