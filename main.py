# Cisco IOS/IOS-XE config -> EXOS (.xsf) translator: command-line entry point.
#   python3 main.py <cisco_config.cfg> [<cisco_config2.cfg> ...]

import sys

from cisco_exos_translator.cli import main

if __name__ == "__main__":
    sys.exit(main())
