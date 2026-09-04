# Cisco IOS/IOS-XE running-config -> Extreme EXOS (.xsf + .pol) translator.
#
# Public API:
#   parse_cisco_config(text)            -> ParsedConfig (IR + warnings)
#   build_default_mapping(config)       -> mapping dict (user-editable decisions)
#   generate_exos_config(config, ...)   -> (xsf text, warnings, {pol name: text})
#   generate_stack_setup(config, name)  -> stack runbook text or None

from .generator import build_default_mapping, generate_exos_config, generate_stack_setup
from .models import ParsedConfig
from .parser import parse_cisco_config

__version__ = "1.0.0"

__all__ = [
    "ParsedConfig",
    "build_default_mapping",
    "generate_exos_config",
    "generate_stack_setup",
    "parse_cisco_config",
    "__version__",
]
