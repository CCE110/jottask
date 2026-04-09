"""
Zoho CRM Connector — Full implementation using Zoho CRM v2 API

Auth header: Zoho-oauthtoken {access_token}
Default base URL: https://www.zohoapis.com.au/crm/v2 (Australian data center)
Supports other regions via settings.zoho_domain
"""

import os
import requests
from datetime import datetime, timedelta
import pytz

from crm_connectors.base import BaseCRMConnector, CRMContact, CRMResult
from crm_connectors.registry import register_connector

REQUEST_TIMEOUT = 15
DEFAULT_API_DOMAIN = 'https://www.zohoapis.com.au'
DEFAULT_ACCOUNTS_DOMAIN = 'https://accounts.zoho.com.au'


class ZohoConnector(BaseCRMConnector):

    PROVIDER = 'zoho'

    def __init__(self, api_key: str = '', api_base_url: str = '',
                 access_token: str = '', settings: dict = None):
        super().__init__(api_key, api_base_url, access_token, settings)
        self.token = access_token or api_key
        # Support regional domains via settings
        zoho_domain = (settings or {}).get('zoho_domain', '')
        self.api_domain = zoho_domain or api_base_url or DEFAULT_API_DOMAIN
        self.api_domain = self.api_domain.rstrip('/')
        self.accounts_domain = (settings or {}).get('zoho_accounts_domain', DEFAULT_ACCOUNTS_DOMAIN).rstrip('/')

    def _headers(self) -> dict:
        return {
            'Authorization': f'Zoho-oauthtoken {self.token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def _url(self, path: str) -> str:
        return f'{self.api_domain}/crm/v2{path}'

    def _get(self, path: str, params: dict = None) -> requests.Response:
        return requests.get(
            self._url(path),
            headers=self._headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

    def _post(self, path: str, json_data: dict = None) -> requests.Response:
        return requests.post(
            self._url(path),
            headers=self._headers(),
            json=json_data,
            timeout=REQUEST_TIMEOUT,
        )

    def _put(self, path: str, json_data: dict = None) -> requests.Response:
        return requests.put(
            self._url(path),
            headers=self._headers(),
            json=json_data,
            timeout=REQUEST_TIMEOUT,
        )

    # ======================================================
    # Required methods
    # ======================================================

    def test_connection(self) -> CRMResult:
        """Test connection by fetching current user info."""
        try:
            resp = requests.get(
                f'{self.api_domain}/crm/v2/users',
                headers=self._headers(),
                params={'type': 'CurrentUser'},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                users = data.get('users', [])
                if users:
                    user_name = users[0].get('full_name', users[0].get('email', 'user'))
                    return CRMResult(
                        success=True,
                        message=f'Connected to Zoho CRM as {user_name}',
                        data=data,
                    )
                return CRMResult(success=True, message='Connected to Zoho CRM', data=data)
            elif resp.status_code == 401:
                return CRMResult(success=False, message='Invalid Zoho token — reconnect OAuth')
            elif resp.status_code == 403:
                return CRMResult(success=False, message='Insufficient Zoho permissions — check OAuth scopes')
            else:
                return CRMResult(
                    success=False,
                    message=f'Zoho returned HTTP {resp.status_code}: {resp.text[:200]}',
                )
        except requests.ConnectionError:
            return CRMResult(success=False, message=f'Cannot reach Zoho API at {self.api_domain}')
        except requests.Timeout:
            return CRMResult(success=False, message='Zoho request timed out')
        except Exception as e:
            return CRMResult(success=False, message=f'Connection error: {str(e)}')

    def find_contact(self, name: str = '', email: str = '') -> CRMResult:
        """Search contacts by email (exact) or name (contains)."""
        try:
            if email:
                criteria = f'(Email:equals:{email})'
            elif name:
                # Zoho search supports word-based matching
                criteria = f'(Full_Name:equals:{name})'
            else:
                return CRMResult(success=False, message='Provide a name or email to search')

            resp = self._get('/Contacts/search', params={'criteria': criteria})

            # Zoho returns 204 No Content when no results found
            if resp.status_code == 204:
                # If exact name match failed, try searching with starts_with
                if name and not email:
                    resp = self._get('/Contacts/search', params={'word': name})
                    if resp.status_code == 204:
                        return CRMResult(success=True, message='No contacts found', contacts=[])
                else:
                    return CRMResult(success=True, message='No contacts found', contacts=[])

            if resp.status_code != 200:
                return CRMResult(success=False, message=f'Search failed: HTTP {resp.status_code}: {resp.text[:200]}')

            data = resp.json()
            items = data.get('data', [])
            return self._parse_contacts(items)

        except Exception as e:
            return CRMResult(success=False, message=f'Contact search failed: {str(e)}')

    def add_note(self, contact_id: str, note_text: str) -> CRMResult:
        """Add a note to a contact in Zoho CRM."""
        try:
            payload = {
                'data': [{
                    'Note_Title': 'Jottask Update',
                    'Note_Content': note_text,
                }]
            }
            resp = self._post(f'/Contacts/{contact_id}/Notes', json_data=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                details = data.get('data', [{}])[0]
                status = details.get('status', '')
                if status == 'error':
                    return CRMResult(success=False, message=f"Zoho error: {details.get('message', 'Unknown error')}")
                return CRMResult(success=True, message='Note added to Zoho contact', data=data)
            else:
                return CRMResult(success=False, message=f'Failed to add note: HTTP {resp.status_code}: {resp.text[:200]}')
        except Exception as e:
            return CRMResult(success=False, message=f'Add note failed: {str(e)}')

    def get_contact_details(self, contact_id: str) -> CRMResult:
        """Get full details for a Zoho contact."""
        try:
            resp = self._get(f'/Contacts/{contact_id}')
            if resp.status_code != 200:
                return CRMResult(success=False, message=f'Contact fetch failed: HTTP {resp.status_code}')

            data = resp.json()
            items = data.get('data', [])
            if not items:
                return CRMResult(success=False, message='Contact not found')

            item = items[0]
            first = item.get('First_Name', '')
            last = item.get('Last_Name', '')
            name = f'{first} {last}'.strip()

            contact = CRMContact(
                id=str(item.get('id', '')),
                name=name,
                email=item.get('Email', ''),
                phone=item.get('Phone', item.get('Mobile', '')),
                company=item.get('Account_Name', {}).get('name', '') if isinstance(item.get('Account_Name'), dict) else item.get('Account_Name', ''),
                raw_data=item,
            )
            return CRMResult(success=True, message='Contact retrieved', contact=contact)
        except Exception as e:
            return CRMResult(success=False, message=f'Contact details failed: {str(e)}')

    # ======================================================
    # Optional methods
    # ======================================================

    def update_deal_stage(self, deal_id: str, stage: str) -> CRMResult:
        """Update a deal's stage in Zoho CRM."""
        try:
            payload = {
                'data': [{
                    'Stage': stage,
                }]
            }
            resp = self._put(f'/Deals/{deal_id}', json_data=payload)
            if resp.status_code == 200:
                data = resp.json()
                details = data.get('data', [{}])[0]
                if details.get('status') == 'error':
                    return CRMResult(success=False, message=f"Zoho error: {details.get('message', 'Unknown error')}")
                return CRMResult(success=True, message=f'Deal updated to stage: {stage}', data=data)
            else:
                return CRMResult(success=False, message=f'Deal update failed: HTTP {resp.status_code}: {resp.text[:200]}')
        except Exception as e:
            return CRMResult(success=False, message=f'Deal update failed: {str(e)}')

    def create_contact(self, name: str, email: str = '', phone: str = '') -> CRMResult:
        """Create a new contact in Zoho CRM."""
        try:
            parts = name.strip().split(' ', 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ''

            contact_data = {
                'First_Name': first_name,
                'Last_Name': last_name or first_name,  # Zoho requires Last_Name
            }
            if email:
                contact_data['Email'] = email
            if phone:
                contact_data['Phone'] = phone

            payload = {'data': [contact_data]}
            resp = self._post('/Contacts', json_data=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                details = data.get('data', [{}])[0]
                if details.get('status') == 'error':
                    return CRMResult(success=False, message=f"Zoho error: {details.get('message', 'Unknown error')}")

                contact_id = details.get('details', {}).get('id', '')
                contact = CRMContact(
                    id=str(contact_id),
                    name=name,
                    email=email,
                    phone=phone,
                    raw_data=details,
                )
                return CRMResult(success=True, message='Contact created in Zoho CRM', contact=contact)
            else:
                return CRMResult(success=False, message=f'Create contact failed: HTTP {resp.status_code}: {resp.text[:200]}')
        except Exception as e:
            return CRMResult(success=False, message=f'Create contact failed: {str(e)}')

    # ======================================================
    # OAuth token refresh
    # ======================================================

    def refresh_access_token(self, refresh_token: str) -> CRMResult:
        """Exchange a refresh token for a new access token.
        Note: Zoho does NOT return a new refresh token — the original persists.
        """
        client_id = os.getenv('ZOHO_CLIENT_ID', '')
        client_secret = os.getenv('ZOHO_CLIENT_SECRET', '')
        if not client_id or not client_secret:
            return CRMResult(success=False, message='ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET not configured')

        try:
            resp = requests.post(
                f'{self.accounts_domain}/oauth/v2/token',
                params={
                    'grant_type': 'refresh_token',
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'refresh_token': refresh_token,
                },
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                return CRMResult(success=False, message=f'Token refresh failed: HTTP {resp.status_code}: {resp.text[:200]}')

            data = resp.json()
            if 'error' in data:
                return CRMResult(success=False, message=f"Token refresh error: {data['error']}")

            expires_in = data.get('expires_in', 3600)
            expires_at = (datetime.now(pytz.UTC) + timedelta(seconds=expires_in)).isoformat()

            return CRMResult(
                success=True,
                message='Token refreshed',
                data={
                    'access_token': data['access_token'],
                    'refresh_token': '',  # Zoho doesn't return new refresh token
                    'token_expires_at': expires_at,
                },
            )
        except Exception as e:
            return CRMResult(success=False, message=f'Token refresh failed: {str(e)}')

    # ======================================================
    # Internal helpers
    # ======================================================

    def _parse_contacts(self, items: list) -> CRMResult:
        """Parse Zoho contact records into CRMContact list."""
        contacts = []
        for item in items:
            first = item.get('First_Name', '')
            last = item.get('Last_Name', '')
            name = f'{first} {last}'.strip()
            company = ''
            acct = item.get('Account_Name')
            if isinstance(acct, dict):
                company = acct.get('name', '')
            elif isinstance(acct, str):
                company = acct

            contacts.append(CRMContact(
                id=str(item.get('id', '')),
                name=name,
                email=item.get('Email', ''),
                phone=item.get('Phone', item.get('Mobile', '')),
                company=company,
                raw_data=item,
            ))

        if contacts:
            return CRMResult(success=True, message=f'Found {len(contacts)} contact(s)', contacts=contacts, contact=contacts[0])
        return CRMResult(success=True, message='No contacts found', contacts=[])


# Auto-register
register_connector('zoho', ZohoConnector)
