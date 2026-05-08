"""fireplace — Monte Carlo retirement simulator."""

__version__ = "0.1.0"

from .case import Case, ScenarioReport
from .config import load_config
from .simulate import simulate

__all__ = ["Case", "ScenarioReport", "load_config", "simulate"]
