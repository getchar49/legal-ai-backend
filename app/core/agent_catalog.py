from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentProfile:
    agent_id: str
    name: str
    description: str
    inference_mode: str
    external_agent_id: str
    legacy_thinking: bool
    enabled: bool = True


DEFAULT_AGENT_ID = "fast"
AGENT_CATALOG: tuple[AgentProfile, ...] = (
    AgentProfile(
        agent_id="fast",
        name="Fast",
        description="Tra loi nhanh cho cau hoi thong thuong.",
        inference_mode="fast",
        external_agent_id="fast",
        legacy_thinking=False,
        enabled=True,
    ),
    AgentProfile(
        agent_id="to_do",
        name="Thinking",
        description="Lap luan ky hon cho cac cau hoi phuc tap.",
        inference_mode="thinking",
        external_agent_id="to_do",
        legacy_thinking=True,
        enabled=True,
    ),
)

AGENT_LOOKUP = {agent.agent_id: agent for agent in AGENT_CATALOG}


def normalize_agent_id(agent_id: str | None) -> str:
    normalized = (agent_id or "").strip().lower()
    return normalized or DEFAULT_AGENT_ID


def resolve_agent_profile(agent_id: str | None) -> AgentProfile:
    normalized = normalize_agent_id(agent_id)
    known_agent = AGENT_LOOKUP.get(normalized)
    if known_agent:
        return known_agent

    # Keep compatibility for unknown IDs: pass through to external service.
    return AgentProfile(
        agent_id=normalized,
        name=normalized,
        description="Custom agent forwarded to external service.",
        inference_mode="custom",
        external_agent_id=normalized,
        legacy_thinking=False,
        enabled=False,
    )


def list_public_agents() -> list[dict[str, Any]]:
    return [
        {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "description": agent.description,
            "inference_mode": agent.inference_mode,
            "is_default": agent.agent_id == DEFAULT_AGENT_ID,
        }
        for agent in AGENT_CATALOG
        if agent.enabled
    ]
