from pathlib import Path
from assuranceos.adk import build_adk_agent


def create_agent(model: str = "gemini-2.5-flash"):
    return build_adk_agent(Path(__file__).parent, model)
