"""DockLens — a zero-dependency Dockerfile auditor."""

from .checks import analyse
from .parser import parse_dockerfile

__all__ = ["analyse", "parse_dockerfile"]
__version__ = "1.0.0"
