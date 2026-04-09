"""
CRM Connector Registry
Factory for creating connector instances by provider name
"""

from typing import Dict, List, Type
from crm_connectors.base import BaseCRMConnector

_REGISTRY: Dict[str, Type[BaseCRMConnector]] = {}


def register_connector(provider: str, connector_class: Type[BaseCRMConnector]):
    """Register a connector class for a provider name."""
    _REGISTRY[provider.lower()] = connector_class


def get_connector(provider: str, **kwargs) -> BaseCRMConnector:
    """Create a connector instance for the given provider.
    kwargs are passed to the connector constructor (api_key, api_base_url, etc).
    Raises ValueError if provider is not registered.
    """
    provider = provider.lower()
    if provider not in _REGISTRY:
        raise ValueError(f"Unknown CRM provider: '{provider}'. Available: {list(_REGISTRY.keys())}")
    return _REGISTRY[provider](**kwargs)


def list_providers() -> List[str]:
    """Return list of registered provider names."""
    return list(_REGISTRY.keys())
