"""
Shared Pydantic models for Network Topology plugin.

These models define the core data structures for network topologies,
networks, hosts, and routers. They are used across different components
of the plugin for validation and serialization.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class HostType(BaseModel):
    """Configuration for a host role type."""
    label: str = Field(..., description="Display label for the host type")
    ports: List[str] = Field(default_factory=list, description="Default port mappings")
    description: str = Field(..., description="Description of the host type purpose")


class FirewallConfig(BaseModel):
    """Firewall configuration for routing between networks."""
    allowed: List[str] = Field(
        default_factory=list,
        description="List of allowed network pairs in format 'source_id->dest_id'"
    )

    @field_validator("allowed")
    @classmethod
    def validate_allowed_pairs(cls, v: List[str]) -> List[str]:
        """Validate firewall allowed pairs format."""
        return [pair for pair in v if isinstance(pair, str) and "->" in pair]


class RouterConfig(BaseModel):
    """Router configuration with SSH and firewall settings."""
    ssh_enabled: bool = Field(default=False, description="Enable SSH access on router")
    username: str = Field(default="admin", description="SSH username")
    password: str = Field(default="strato", description="SSH password")
    firewall: FirewallConfig = Field(
        default_factory=FirewallConfig,
        description="Firewall rules for network-to-network traffic"
    )


class Router(BaseModel):
    """A router in the topology hierarchy."""
    id: str = Field(..., description="Unique router identifier")
    name: str = Field(..., description="Router display name")
    parent_router_id: str = Field(
        default="", description="ID of parent router (empty for root router)"
    )
    ssh_enabled: bool = Field(default=False, description="Enable SSH on this router")
    username: str = Field(default="admin", description="SSH username")
    password: str = Field(default="strato", description="SSH password")


class Host(BaseModel):
    """A host (container) in a network."""
    id: str = Field(..., description="Unique host identifier within network")
    name: str = Field(..., description="Host display name")
    type: str = Field(
        default="normal-user",
        description="Host role type (web-server, db, file-server, etc.)"
    )
    image: str = Field(default="ubuntu:24.04", description="Container base image")
    ssh_enabled: bool = Field(default=False, description="Enable SSH access on host")
    username: str = Field(default="student", description="SSH username")
    password: str = Field(default="strato", description="SSH password")
    generate_data: bool = Field(
        default=False, description="Generate AI data for this host"
    )
    data_prompt: str = Field(
        default="", description="Prompt for AI data generation"
    )
    data_content: str = Field(default="", description="Generated data content")
    agents: List[str] = Field(
        default_factory=list,
        description="List of OpenCode agents assigned to this host"
    )

    # Legacy fields for compatibility
    agent_enabled: Optional[bool] = Field(
        default=None, description="Legacy: single agent enabled flag"
    )
    agent_type: Optional[str] = Field(
        default=None, description="Legacy: single agent type"
    )


class Network(BaseModel):
    """A network segment in the topology."""
    id: str = Field(..., description="Unique network identifier")
    name: str = Field(..., description="Network display name")
    cidr: str = Field(..., description="Network CIDR block (e.g., '10.77.1.0/24')")
    internet: bool = Field(
        default=False, description="Allow internet egress from this network"
    )
    hosts: List[Host] = Field(default_factory=list, description="Hosts in this network")
    router_ids: List[str] = Field(
        default_factory=list,
        description="Router IDs attached to this network"
    )
    default_router_id: str = Field(
        default="", description="Primary router used as default gateway"
    )

    # Legacy field for compatibility
    router_id: Optional[str] = Field(
        default=None, description="Legacy: single router ID"
    )


class Infrastructure(BaseModel):
    """Infrastructure settings for the topology."""
    hackerlab_network_id: str = Field(
        ...,
        description="Network ID where scl-hackerlab container attaches"
    )


class OpenCodeAgent(BaseModel):
    """OpenCode agent integration settings."""
    enabled: bool = Field(default=False, description="Enable OpenCode agent support")


class SlipsMonitoring(BaseModel):
    """SLIPS IDS sensor + autonomous defender settings.

    NOTE: the enforced schema is ``validate_topology`` in app.py (this model is
    documentation). SLIPS is opt-in; when enabled a slips-sensor sidecar and a
    shared pcaps volume are added to the topology's compose, and the
    ``capture_source`` router tcpdumps traffic for analysis.
    """
    enabled: bool = Field(default=False, description="Enable the SLIPS sensor sidecar")
    capture_source: str = Field(
        default="",
        description="Router id/name whose traffic is captured (defaults to the root router)"
    )
    defender_enabled: bool = Field(
        default=True,
        description="Whether soc_god should auto-respond to the sensor's alerts"
    )


class Monitoring(BaseModel):
    """Topology-level monitoring options."""
    slips: SlipsMonitoring = Field(default_factory=SlipsMonitoring, description="SLIPS settings")


class VisualLayout(BaseModel):
    """Visual layout data for UI rendering."""
    routers: Dict[str, Dict[str, float]] = Field(
        default_factory=dict,
        description="Router positions {router_id: {x, y}}"
    )
    networks: Dict[str, Dict[str, float]] = Field(
        default_factory=dict,
        description="Network positions {network_id: {x, y}}"
    )


class Topology(BaseModel):
    """Complete network topology configuration."""
    id: Optional[str] = Field(None, description="Unique topology identifier")
    name: str = Field(..., description="Topology display name")
    created_at: Optional[str] = Field(None, description="ISO timestamp of creation")
    updated_at: Optional[str] = Field(None, description="ISO timestamp of last update")
    routers: List[Router] = Field(default_factory=list, description="Routers in topology")
    router: RouterConfig = Field(
        default_factory=RouterConfig,
        description="Global router configuration"
    )
    infrastructure: Infrastructure = Field(
        default_factory=lambda: Infrastructure(hackerlab_network_id=""),
        description="Infrastructure settings"
    )
    opencode_agent: OpenCodeAgent = Field(
        default_factory=OpenCodeAgent,
        description="OpenCode agent integration settings"
    )
    monitoring: Monitoring = Field(
        default_factory=Monitoring,
        description="Monitoring options (SLIPS IDS sensor)"
    )
    networks: List[Network] = Field(
        default_factory=list,
        description="Network segments in the topology"
    )
    visual: VisualLayout = Field(
        default_factory=VisualLayout,
        description="Visual layout coordinates for UI"
    )


class TopologySummary(BaseModel):
    """Summary of a topology for listing endpoints."""
    id: str
    name: str
    created_at: Optional[str]
    updated_at: Optional[str]
    networks: int = Field(..., description="Number of networks")
    hosts: int = Field(..., description="Total number of hosts")
    running: bool = Field(default=False, description="Whether topology is currently running")


class JobStatus(BaseModel):
    """Background job status."""
    id: str
    status: str = Field(..., description="Job status: running, completed, failed")
    result: Optional[Dict[str, Any]] = Field(None, description="Job result data")
    error: str = Field(default="", description="Error message if failed")


class DataGenerationRequest(BaseModel):
    """Request to generate AI data for a host."""
    host: Host
    topology: Topology


class DataGenerationResponse(BaseModel):
    """Response from AI data generation."""
    content: str = Field(..., description="Generated data content")
