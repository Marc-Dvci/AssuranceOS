from pathlib import Path
from assuranceos.registry import AgentRegistry


def test_all_agent_packages_load():
    root = Path(__file__).resolve().parents[1]
    packages = AgentRegistry(root / "agents").load()
    assert len(packages) == 19
    assert "skeptic" in packages
    assert packages["finding-adjudicator"].manifest["human_gates"] == ["finding_approval"]
