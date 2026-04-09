"""
CRM Connectors Package
Pluggable CRM integration system for Jottask SaaS
"""

from crm_connectors.base import BaseCRMConnector, CRMContact, CRMDeal, CRMResult
from crm_connectors.registry import get_connector, register_connector, list_providers
from crm_connectors.pipereply import PipeReplyConnector
from crm_connectors.hubspot import HubSpotConnector
from crm_connectors.zoho import ZohoConnector

__all__ = [
    'BaseCRMConnector', 'CRMContact', 'CRMDeal', 'CRMResult',
    'get_connector', 'register_connector', 'list_providers',
    'PipeReplyConnector', 'HubSpotConnector', 'ZohoConnector',
]
