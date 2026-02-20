"""
PipeReply CRM Connector

API paths below are PLACEHOLDERS based on common REST CRM patterns.
Update these constants once the real PipeReply API paths are verified.
test_connection() will catch wrong paths immediately during setup.
"""

import requests
from crm_connectors.base import BaseCRMConnector, CRMContact, CRMResult
from crm_connectors.registry import register_connector

# Default timeout for all API calls (seconds)
REQUEST_TIMEOUT = 15


class PipeReplyConnector(BaseCRMConnector):

    PROVIDER = 'pipereply'

    # ======================================================
    # API PATH CONSTANTS — update these with real endpoints
    # ======================================================
    PATH_ME = '/api/v1/me'                          # Test connection / current user
    PATH_CONTACTS = '/api/v1/contacts'              # List / search contacts
    PATH_CONTACT = '/api/v1/contacts/{contact_id}'  # Single contact
    PATH_NOTES = '/api/v1/contacts/{contact_id}/notes'  # Add note to contact
    PATH_DEALS = '/api/v1/deals'                    # List deals
    PATH_DEAL = '/api/v1/deals/{deal_id}'           # Single deal / update stage

    # Default base URL — user can override in settings
    DEFAULT_BASE_URL = 'https://app.pipereply.com'

    def __init__(self, api_key: str = '', api_base_url: str = '',
                 access_token: str = '', settings: dict = None):
        super().__init__(api_key, api_base_url, access_token, settings)
        self.base_url = (api_base_url or self.DEFAULT_BASE_URL).rstrip('/')

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def _url(self, path: str, **kwargs) -> str:
        return self.base_url + path.format(**kwargs)

    def _get(self, path: str, params: dict = None, **path_kwargs) -> requests.Response:
        return requests.get(
            self._url(path, **path_kwargs),
            headers=self._headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

    def _post(self, path: str, json_data: dict = None, **path_kwargs) -> requests.Response:
        return requests.post(
            self._url(path, **path_kwargs),
            headers=self._headers(),
            json=json_data,
            timeout=REQUEST_TIMEOUT,
        )

    def _put(self, path: str, json_data: dict = None, **path_kwargs) -> requests.Response:
        return requests.put(
            self._url(path, **path_kwargs),
            headers=self._headers(),
            json=json_data,
            timeout=REQUEST_TIMEOUT,
        )

    # ======================================================
    # Required methods
    # ======================================================

    def test_connection(self) -> CRMResult:
        """Test API key by fetching current user / account info."""
        try:
            resp = self._get(self.PATH_ME)
            if resp.status_code == 200:
                data = resp.json()
                return CRMResult(
                    success=True,
                    message=f"Connected to PipeReply as {data.get('name', data.get('email', 'user'))}",
                    data=data,
                )
            elif resp.status_code == 401:
                return CRMResult(success=False, message='Invalid API key')
            else:
                return CRMResult(
                    success=False,
                    message=f'PipeReply returned HTTP {resp.status_code}: {resp.text[:200]}',
                )
        except requests.ConnectionError:
            return CRMResult(success=False, message=f'Cannot reach PipeReply at {self.base_url}')
        except requests.Timeout:
            return CRMResult(success=False, message='PipeReply request timed out')
        except Exception as e:
            return CRMResult(success=False, message=f'Connection error: {str(e)}')

    def find_contact(self, name: str = '', email: str = '') -> CRMResult:
        """Search contacts by name or email."""
        try:
            params = {}
            if email:
                params['email'] = email
            if name:
                params['search'] = name

            resp = self._get(self.PATH_CONTACTS, params=params)
            if resp.status_code != 200:
                return CRMResult(success=False, message=f'Search failed: HTTP {resp.status_code}')

            data = resp.json()
            # Handle both list response and paginated {data: [...]} response
            items = data if isinstance(data, list) else data.get('data', data.get('contacts', []))

            contacts = []
            for item in items:
                contacts.append(CRMContact(
                    id=str(item.get('id', '')),
                    name=item.get('name', item.get('full_name', '')),
                    email=item.get('email', ''),
                    phone=item.get('phone', item.get('mobile', '')),
                    company=item.get('company', item.get('organization', '')),
                    raw_data=item,
                ))

            if contacts:
                return CRMResult(success=True, message=f'Found {len(contacts)} contact(s)', contacts=contacts, contact=contacts[0])
            return CRMResult(success=True, message='No contacts found', contacts=[])

        except Exception as e:
            return CRMResult(success=False, message=f'Contact search failed: {str(e)}')

    def add_note(self, contact_id: str, note_text: str) -> CRMResult:
        """Add a note to a contact."""
        try:
            resp = self._post(
                self.PATH_NOTES,
                json_data={'content': note_text, 'body': note_text},
                contact_id=contact_id,
            )
            if resp.status_code in (200, 201):
                return CRMResult(success=True, message='Note added to PipeReply', data=resp.json())
            else:
                return CRMResult(success=False, message=f'Failed to add note: HTTP {resp.status_code}')
        except Exception as e:
            return CRMResult(success=False, message=f'Add note failed: {str(e)}')

    def get_contact_details(self, contact_id: str) -> CRMResult:
        """Get full details for a contact."""
        try:
            resp = self._get(self.PATH_CONTACT, contact_id=contact_id)
            if resp.status_code != 200:
                return CRMResult(success=False, message=f'Contact fetch failed: HTTP {resp.status_code}')

            item = resp.json()
            # Handle wrapped response
            if 'data' in item and isinstance(item['data'], dict):
                item = item['data']

            contact = CRMContact(
                id=str(item.get('id', '')),
                name=item.get('name', item.get('full_name', '')),
                email=item.get('email', ''),
                phone=item.get('phone', item.get('mobile', '')),
                company=item.get('company', item.get('organization', '')),
                raw_data=item,
            )
            return CRMResult(success=True, message='Contact retrieved', contact=contact)
        except Exception as e:
            return CRMResult(success=False, message=f'Contact details failed: {str(e)}')

    # ======================================================
    # Optional methods
    # ======================================================

    def update_deal_stage(self, deal_id: str, stage: str) -> CRMResult:
        """Update a deal's pipeline stage."""
        try:
            resp = self._put(
                self.PATH_DEAL,
                json_data={'stage': stage, 'status': stage},
                deal_id=deal_id,
            )
            if resp.status_code in (200, 201):
                return CRMResult(success=True, message=f'Deal updated to stage: {stage}', data=resp.json())
            else:
                return CRMResult(success=False, message=f'Deal update failed: HTTP {resp.status_code}')
        except Exception as e:
            return CRMResult(success=False, message=f'Deal update failed: {str(e)}')

    def create_contact(self, name: str, email: str = '', phone: str = '') -> CRMResult:
        """Create a new contact in PipeReply."""
        try:
            payload = {'name': name}
            if email:
                payload['email'] = email
            if phone:
                payload['phone'] = phone

            resp = self._post(self.PATH_CONTACTS, json_data=payload)
            if resp.status_code in (200, 201):
                item = resp.json()
                if 'data' in item and isinstance(item['data'], dict):
                    item = item['data']
                contact = CRMContact(
                    id=str(item.get('id', '')),
                    name=item.get('name', name),
                    email=item.get('email', email),
                    phone=item.get('phone', phone),
                    raw_data=item,
                )
                return CRMResult(success=True, message='Contact created', contact=contact)
            else:
                return CRMResult(success=False, message=f'Create contact failed: HTTP {resp.status_code}')
        except Exception as e:
            return CRMResult(success=False, message=f'Create contact failed: {str(e)}')


# Auto-register with the connector registry
register_connector('pipereply', PipeReplyConnector)
