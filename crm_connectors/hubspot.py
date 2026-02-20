"""
HubSpot CRM Connector — Stub (Coming Soon)

All methods return 'coming soon' messages.
Will be implemented when HubSpot integration is prioritised.
"""

from crm_connectors.base import BaseCRMConnector, CRMResult
from crm_connectors.registry import register_connector


class HubSpotConnector(BaseCRMConnector):

    PROVIDER = 'hubspot'

    def test_connection(self) -> CRMResult:
        return CRMResult(success=False, message='HubSpot integration coming soon')

    def find_contact(self, name: str = '', email: str = '') -> CRMResult:
        return CRMResult(success=False, message='HubSpot integration coming soon')

    def add_note(self, contact_id: str, note_text: str) -> CRMResult:
        return CRMResult(success=False, message='HubSpot integration coming soon')

    def get_contact_details(self, contact_id: str) -> CRMResult:
        return CRMResult(success=False, message='HubSpot integration coming soon')


# Auto-register
register_connector('hubspot', HubSpotConnector)
