"""
Base CRM Connector - Abstract interface for all CRM integrations
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CRMContact:
    """Standardised contact representation across CRM providers"""
    id: str = ''
    name: str = ''
    email: str = ''
    phone: str = ''
    company: str = ''
    raw_data: dict = field(default_factory=dict)


@dataclass
class CRMDeal:
    """Standardised deal/opportunity representation"""
    id: str = ''
    title: str = ''
    stage: str = ''
    value: float = 0.0
    contact_id: str = ''
    raw_data: dict = field(default_factory=dict)


@dataclass
class CRMResult:
    """Result wrapper for all CRM operations"""
    success: bool
    message: str = ''
    data: Optional[dict] = None
    contact: Optional[CRMContact] = None
    deal: Optional[CRMDeal] = None
    contacts: list = field(default_factory=list)


class BaseCRMConnector(ABC):
    """Abstract base class for CRM connectors.

    Every connector must implement test_connection, find_contact, add_note,
    and get_contact_details. Optional methods have default implementations
    that return 'not supported'.
    """

    PROVIDER = 'base'  # Override in subclass

    def __init__(self, api_key: str = '', api_base_url: str = '',
                 access_token: str = '', settings: dict = None):
        self.api_key = api_key
        self.api_base_url = api_base_url
        self.access_token = access_token
        self.settings = settings or {}

    @abstractmethod
    def test_connection(self) -> CRMResult:
        """Test that the connection credentials work.
        Should make a lightweight API call (e.g. get current user).
        """
        pass

    @abstractmethod
    def find_contact(self, name: str = '', email: str = '') -> CRMResult:
        """Search for a contact by name and/or email.
        Returns CRMResult with contacts list populated.
        """
        pass

    @abstractmethod
    def add_note(self, contact_id: str, note_text: str) -> CRMResult:
        """Add a note/activity to a contact."""
        pass

    @abstractmethod
    def get_contact_details(self, contact_id: str) -> CRMResult:
        """Get full details for a specific contact."""
        pass

    def update_deal_stage(self, deal_id: str, stage: str) -> CRMResult:
        """Update a deal/opportunity stage. Optional — override if supported."""
        return CRMResult(success=False, message=f'{self.PROVIDER} does not support deal stage updates')

    def create_contact(self, name: str, email: str = '', phone: str = '') -> CRMResult:
        """Create a new contact. Optional — override if supported."""
        return CRMResult(success=False, message=f'{self.PROVIDER} does not support contact creation')
