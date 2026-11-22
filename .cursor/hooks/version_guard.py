import runpy
import sys
from pathlib import Path


def main() -> None:
    here = Path(__file__).resolve()
    repo = here.parents[2]
    direct = repo / "tools" / "version_guard.py"
    nested = repo / "PAROL6-Desktop-robot-arm" / "PAROL6 wiring test" / "tools" / "version_guard.py"
    target = direct if direct.is_file() else nested
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if not target.is_file():
        if mode == "cursor-before-shell":
            sys.stdout.write('{"permission":"allow"}\n')
        else:
            sys.stdout.write("{}\n")
        return
    sys.argv[0] = str(target)
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
