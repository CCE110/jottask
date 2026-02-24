"""
HubSpot CRM Connector — Full implementation using HubSpot CRM v3 API

Supports both OAuth 2.0 tokens and private app tokens.
Auth header: Bearer {access_token or api_key}
Base URL: https://api.hubapi.com
"""

import os
import requests
from datetime import datetime, timedelta
import pytz

from crm_connectors.base import BaseCRMConnector, CRMContact, CRMResult
from crm_connectors.registry import register_connector

REQUEST_TIMEOUT = 15
BASE_URL = 'https://api.hubapi.com'


class HubSpotConnector(BaseCRMConnector):

    PROVIDER = 'hubspot'

    def __init__(self, api_key: str = '', api_base_url: str = '',
                 access_token: str = '', settings: dict = None):
        super().__init__(api_key, api_base_url, access_token, settings)
        # HubSpot uses Bearer token — prefer access_token (OAuth), fall back to api_key (private app)
        self.token = access_token or api_key

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def _get(self, path: str, params: dict = None) -> requests.Response:
        return requests.get(
            f'{BASE_URL}{path}',
            headers=self._headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

    def _post(self, path: str, json_data: dict = None) -> requests.Response:
        return requests.post(
            f'{BASE_URL}{path}',
            headers=self._headers(),
            json=json_data,
            timeout=REQUEST_TIMEOUT,
        )

    def _patch(self, path: str, json_data: dict = None) -> requests.Response:
        return requests.patch(
            f'{BASE_URL}{path}',
            headers=self._headers(),
            json=json_data,
            timeout=REQUEST_TIMEOUT,
        )

    def _put(self, path: str, json_data: dict = None) -> requests.Response:
        return requests.put(
            f'{BASE_URL}{path}',
            headers=self._headers(),
            json=json_data,
            timeout=REQUEST_TIMEOUT,
        )

    # ======================================================
    # Required methods
    # ======================================================

    def test_connection(self) -> CRMResult:
        """Lightweight auth check — fetch one contact to verify credentials."""
        try:
            resp = self._get('/crm/v3/objects/contacts', params={'limit': 1})
            if resp.status_code == 200:
                data = resp.json()
                total = data.get('total', 0)
                return CRMResult(
                    success=True,
                    message=f'Connected to HubSpot ({total} contacts)',
                    data=data,
                )
            elif resp.status_code == 401:
                return CRMResult(success=False, message='Invalid HubSpot token — check your API key or reconnect OAuth')
            elif resp.status_code == 403:
                return CRMResult(success=False, message='Insufficient HubSpot permissions — ensure contacts scope is granted')
            else:
                return CRMResult(
                    success=False,
                    message=f'HubSpot returned HTTP {resp.status_code}: {resp.text[:200]}',
                )
        except requests.ConnectionError:
            return CRMResult(success=False, message='Cannot reach HubSpot API')
        except requests.Timeout:
            return CRMResult(success=False, message='HubSpot request timed out')
        except Exception as e:
            return CRMResult(success=False, message=f'Connection error: {str(e)}')

    def find_contact(self, name: str = '', email: str = '') -> CRMResult:
        """Search contacts using HubSpot CRM Search API.
        Prefers email (exact match) over name (token search).
        """
        try:
            filters = []
            if email:
                filters.append({
                    'propertyName': 'email',
                    'operator': 'EQ',
                    'value': email,
                })
            elif name:
                # Use the search query for name (token-based search)
                payload = {
                    'query': name,
                    'limit': 10,
                    'properties': ['firstname', 'lastname', 'email', 'phone', 'company'],
                }
                resp = self._post('/crm/v3/objects/contacts/search', json_data=payload)
                if resp.status_code != 200:
                    return CRMResult(success=False, message=f'Search failed: HTTP {resp.status_code}')

                data = resp.json()
                return self._parse_contacts(data.get('results', []))

            if filters:
                payload = {
                    'filterGroups': [{'filters': filters}],
                    'limit': 10,
                    'properties': ['firstname', 'lastname', 'email', 'phone', 'company'],
                }
                resp = self._post('/crm/v3/objects/contacts/search', json_data=payload)
                if resp.status_code != 200:
                    return CRMResult(success=False, message=f'Search failed: HTTP {resp.status_code}')

                data = resp.json()
                return self._parse_contacts(data.get('results', []))

            return CRMResult(success=False, message='Provide a name or email to search')

        except Exception as e:
            return CRMResult(success=False, message=f'Contact search failed: {str(e)}')

    def add_note(self, contact_id: str, note_text: str) -> CRMResult:
        """Add a note to a contact (two-step: create note, then associate)."""
        try:
            # Step 1: Create the note engagement
            now_ms = str(int(datetime.now(pytz.UTC).timestamp() * 1000))
            note_payload = {
                'properties': {
                    'hs_timestamp': now_ms,
                    'hs_note_body': note_text,
                }
            }
            resp = self._post('/crm/v3/objects/notes', json_data=note_payload)
            if resp.status_code not in (200, 201):
                return CRMResult(success=False, message=f'Failed to create note: HTTP {resp.status_code}: {resp.text[:200]}')

            note_id = resp.json().get('id')
            if not note_id:
                return CRMResult(success=False, message='Note created but no ID returned')

            # Step 2: Associate note with contact
            assoc_resp = self._put(
                f'/crm/v4/objects/notes/{note_id}/associations/contacts/{contact_id}',
                json_data=[{
                    'associationCategory': 'HUBSPOT_DEFINED',
                    'associationTypeId': 202,  # note_to_contact
                }],
            )
            if assoc_resp.status_code not in (200, 201):
                # Note was created but association failed — still partial success
                return CRMResult(
                    success=True,
                    message=f'Note created (ID: {note_id}) but association failed: HTTP {assoc_resp.status_code}',
                    data={'note_id': note_id},
                )

            return CRMResult(
                success=True,
                message='Note added to HubSpot contact',
                data={'note_id': note_id},
            )

        except Exception as e:
            return CRMResult(success=False, message=f'Add note failed: {str(e)}')

    def get_contact_details(self, contact_id: str) -> CRMResult:
        """Get full details for a HubSpot contact."""
        try:
            props = 'firstname,lastname,email,phone,company,mobilephone,address,city,state,zip,lifecyclestage'
            resp = self._get(f'/crm/v3/objects/contacts/{contact_id}', params={'properties': props})
            if resp.status_code != 200:
                return CRMResult(success=False, message=f'Contact fetch failed: HTTP {resp.status_code}')

            item = resp.json()
            properties = item.get('properties', {})
            first = properties.get('firstname', '')
            last = properties.get('lastname', '')
            name = f'{first} {last}'.strip()

            contact = CRMContact(
                id=str(item.get('id', '')),
                name=name,
                email=properties.get('email', ''),
                phone=properties.get('phone', properties.get('mobilephone', '')),
                company=properties.get('company', ''),
                raw_data=item,
            )
            return CRMResult(success=True, message='Contact retrieved', contact=contact)
        except Exception as e:
            return CRMResult(success=False, message=f'Contact details failed: {str(e)}')

    # ======================================================
    # Optional methods
    # ======================================================

    def update_deal_stage(self, deal_id: str, stage: str) -> CRMResult:
        """Update a deal's pipeline stage in HubSpot."""
        try:
            resp = self._patch(
                f'/crm/v3/objects/deals/{deal_id}',
                json_data={'properties': {'dealstage': stage}},
            )
            if resp.status_code == 200:
                return CRMResult(success=True, message=f'Deal updated to stage: {stage}', data=resp.json())
            else:
                return CRMResult(success=False, message=f'Deal update failed: HTTP {resp.status_code}: {resp.text[:200]}')
        except Exception as e:
            return CRMResult(success=False, message=f'Deal update failed: {str(e)}')

    def create_contact(self, name: str, email: str = '', phone: str = '') -> CRMResult:
        """Create a new contact in HubSpot."""
        try:
            # Split name into first/last
            parts = name.strip().split(' ', 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ''

            properties = {
                'firstname': first_name,
                'lastname': last_name,
            }
            if email:
                properties['email'] = email
            if phone:
                properties['phone'] = phone

            resp = self._post('/crm/v3/objects/contacts', json_data={'properties': properties})
            if resp.status_code in (200, 201):
                item = resp.json()
                props = item.get('properties', {})
                contact = CRMContact(
                    id=str(item.get('id', '')),
                    name=f"{props.get('firstname', '')} {props.get('lastname', '')}".strip(),
                    email=props.get('email', email),
                    phone=props.get('phone', phone),
                    raw_data=item,
                )
                return CRMResult(success=True, message='Contact created in HubSpot', contact=contact)
            elif resp.status_code == 409:
                return CRMResult(success=False, message='Contact already exists in HubSpot')
            else:
                return CRMResult(success=False, message=f'Create contact failed: HTTP {resp.status_code}: {resp.text[:200]}')
        except Exception as e:
            return CRMResult(success=False, message=f'Create contact failed: {str(e)}')

    # ======================================================
    # OAuth token refresh
    # ======================================================

    def refresh_access_token(self, refresh_token: str) -> CRMResult:
        """Exchange a refresh token for a new access token using HubSpot OAuth."""
        client_id = os.getenv('HUBSPOT_CLIENT_ID', '')
        client_secret = os.getenv('HUBSPOT_CLIENT_SECRET', '')
        if not client_id or not client_secret:
            return CRMResult(success=False, message='HUBSPOT_CLIENT_ID and HUBSPOT_CLIENT_SECRET not configured')

        try:
            resp = requests.post(
                f'{BASE_URL}/oauth/v1/token',
                data={
                    'grant_type': 'refresh_token',
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'refresh_token': refresh_token,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                return CRMResult(success=False, message=f'Token refresh failed: HTTP {resp.status_code}: {resp.text[:200]}')

            data = resp.json()
            expires_in = data.get('expires_in', 21600)  # Default 6 hours
            expires_at = (datetime.now(pytz.UTC) + timedelta(seconds=expires_in)).isoformat()

            return CRMResult(
                success=True,
                message='Token refreshed',
                data={
                    'access_token': data['access_token'],
                    'refresh_token': data.get('refresh_token', refresh_token),
                    'token_expires_at': expires_at,
                },
            )
        except Exception as e:
            return CRMResult(success=False, message=f'Token refresh failed: {str(e)}')

    # ======================================================
    # Internal helpers
    # ======================================================

    def _parse_contacts(self, results: list) -> CRMResult:
        """Parse HubSpot contact search results into CRMContact list."""
        contacts = []
        for item in results:
            props = item.get('properties', {})
            first = props.get('firstname', '')
            last = props.get('lastname', '')
            name = f'{first} {last}'.strip()
            contacts.append(CRMContact(
                id=str(item.get('id', '')),
                name=name,
                email=props.get('email', ''),
                phone=props.get('phone', ''),
                company=props.get('company', ''),
                raw_data=item,
            ))

        if contacts:
            return CRMResult(success=True, message=f'Found {len(contacts)} contact(s)', contacts=contacts, contact=contacts[0])
        return CRMResult(success=True, message='No contacts found', contacts=[])


# Auto-register
register_connector('hubspot', HubSpotConnector)
