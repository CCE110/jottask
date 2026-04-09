"""
PipeReply CRM Connector — GoHighLevel (GHL) API v2

PipeReply is a white-label GoHighLevel instance.
Base URL: https://services.leadconnectorhq.com
Auth: Private Integration Token (Bearer) or OAuth access token
Required header: Version: 2021-07-28

For SaaS subscribers: they generate a Private Integration Token from
Settings > Integrations > Private Integrations in their GHL/PipeReply account.
"""

import requests
from crm_connectors.base import BaseCRMConnector, CRMContact, CRMDeal, CRMResult
from crm_connectors.registry import register_connector

REQUEST_TIMEOUT = 15
BASE_URL = 'https://services.leadconnectorhq.com'
API_VERSION = '2021-07-28'


class PipeReplyConnector(BaseCRMConnector):

    PROVIDER = 'pipereply'

    def __init__(self, api_key: str = '', api_base_url: str = '',
                 access_token: str = '', settings: dict = None):
        super().__init__(api_key, api_base_url, access_token, settings)
        # Private Integration Token or OAuth access token
        self.token = access_token or api_key
        self.location_id = (settings or {}).get('location_id', '')

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Version': API_VERSION,
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
        """Test credentials by searching for one contact."""
        try:
            params = {'limit': 1}
            if self.location_id:
                params['locationId'] = self.location_id

            resp = self._get('/contacts/', params=params)
            if resp.status_code == 200:
                data = resp.json()
                total = data.get('meta', {}).get('total', data.get('total', '?'))
                return CRMResult(
                    success=True,
                    message=f'Connected to PipeReply/GHL ({total} contacts)',
                    data=data,
                )
            elif resp.status_code == 401:
                return CRMResult(success=False, message='Invalid token — check your Private Integration Token')
            elif resp.status_code == 422:
                return CRMResult(
                    success=False,
                    message='Missing locationId — add your Location ID in CRM settings',
                )
            else:
                return CRMResult(
                    success=False,
                    message=f'PipeReply returned HTTP {resp.status_code}: {resp.text[:300]}',
                )
        except requests.ConnectionError:
            return CRMResult(success=False, message='Cannot reach GoHighLevel API')
        except requests.Timeout:
            return CRMResult(success=False, message='PipeReply request timed out')
        except Exception as e:
            return CRMResult(success=False, message=f'Connection error: {str(e)}')

    def find_contact(self, name: str = '', email: str = '') -> CRMResult:
        """Search contacts by name or email using GHL v2 search."""
        try:
            params = {'limit': 10}
            if self.location_id:
                params['locationId'] = self.location_id
            if email:
                params['query'] = email
            elif name:
                params['query'] = name
            else:
                return CRMResult(success=False, message='Provide a name or email to search')

            resp = self._get('/contacts/', params=params)
            if resp.status_code != 200:
                return CRMResult(success=False, message=f'Search failed: HTTP {resp.status_code}: {resp.text[:200]}')

            data = resp.json()
            items = data.get('contacts', [])

            contacts = []
            for item in items:
                first = item.get('firstName', '')
                last = item.get('lastName', '')
                full_name = item.get('name', f'{first} {last}'.strip())
                contacts.append(CRMContact(
                    id=str(item.get('id', '')),
                    name=full_name,
                    email=item.get('email', ''),
                    phone=item.get('phone', ''),
                    company=item.get('companyName', ''),
                    raw_data=item,
                ))

            if contacts:
                return CRMResult(success=True, message=f'Found {len(contacts)} contact(s)', contacts=contacts, contact=contacts[0])
            return CRMResult(success=True, message='No contacts found', contacts=[])

        except Exception as e:
            return CRMResult(success=False, message=f'Contact search failed: {str(e)}')

    def add_note(self, contact_id: str, note_text: str) -> CRMResult:
        """Add a note to a contact via GHL v2 Notes API."""
        try:
            resp = self._post(
                f'/contacts/{contact_id}/notes',
                json_data={'body': note_text},
            )
            if resp.status_code in (200, 201):
                return CRMResult(success=True, message='Note added to PipeReply', data=resp.json())
            else:
                return CRMResult(success=False, message=f'Failed to add note: HTTP {resp.status_code}: {resp.text[:200]}')
        except Exception as e:
            return CRMResult(success=False, message=f'Add note failed: {str(e)}')

    def get_contact_details(self, contact_id: str) -> CRMResult:
        """Get full details for a contact by ID."""
        try:
            resp = self._get(f'/contacts/{contact_id}')
            if resp.status_code != 200:
                return CRMResult(success=False, message=f'Contact fetch failed: HTTP {resp.status_code}')

            data = resp.json()
            item = data.get('contact', data)

            first = item.get('firstName', '')
            last = item.get('lastName', '')
            full_name = item.get('name', f'{first} {last}'.strip())

            contact = CRMContact(
                id=str(item.get('id', '')),
                name=full_name,
                email=item.get('email', ''),
                phone=item.get('phone', ''),
                company=item.get('companyName', ''),
                raw_data=item,
            )
            return CRMResult(success=True, message='Contact retrieved', contact=contact)
        except Exception as e:
            return CRMResult(success=False, message=f'Contact details failed: {str(e)}')

    # ======================================================
    # Optional methods
    # ======================================================

    def update_deal_stage(self, deal_id: str, stage: str) -> CRMResult:
        """Update an opportunity's status/stage in GHL."""
        try:
            resp = self._put(
                f'/opportunities/{deal_id}/status',
                json_data={'status': stage},
            )
            if resp.status_code in (200, 201):
                return CRMResult(success=True, message=f'Opportunity updated to: {stage}', data=resp.json())
            else:
                return CRMResult(success=False, message=f'Opportunity update failed: HTTP {resp.status_code}: {resp.text[:200]}')
        except Exception as e:
            return CRMResult(success=False, message=f'Opportunity update failed: {str(e)}')

    def create_contact(self, name: str, email: str = '', phone: str = '') -> CRMResult:
        """Create a new contact in GHL."""
        try:
            parts = name.strip().split(' ', 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ''

            payload = {
                'firstName': first_name,
                'lastName': last_name,
            }
            if email:
                payload['email'] = email
            if phone:
                payload['phone'] = phone
            if self.location_id:
                payload['locationId'] = self.location_id

            resp = self._post('/contacts/', json_data=payload)
            if resp.status_code in (200, 201):
                item = resp.json().get('contact', resp.json())
                contact = CRMContact(
                    id=str(item.get('id', '')),
                    name=f"{item.get('firstName', '')} {item.get('lastName', '')}".strip(),
                    email=item.get('email', email),
                    phone=item.get('phone', phone),
                    raw_data=item,
                )
                return CRMResult(success=True, message='Contact created in PipeReply', contact=contact)
            else:
                return CRMResult(success=False, message=f'Create contact failed: HTTP {resp.status_code}: {resp.text[:200]}')
        except Exception as e:
            return CRMResult(success=False, message=f'Create contact failed: {str(e)}')

    def get_pipelines(self) -> CRMResult:
        """List all pipelines and their stages."""
        try:
            params = {}
            if self.location_id:
                params['locationId'] = self.location_id

            resp = self._get('/opportunities/pipelines', params=params)
            if resp.status_code != 200:
                return CRMResult(success=False, message=f'Pipelines fetch failed: HTTP {resp.status_code}')
            return CRMResult(success=True, message='Pipelines retrieved', data=resp.json())
        except Exception as e:
            return CRMResult(success=False, message=f'Pipelines fetch failed: {str(e)}')

    def search_opportunities(self, pipeline_id: str = '', status: str = '',
                              contact_id: str = '') -> CRMResult:
        """Search opportunities/deals."""
        try:
            params = {'limit': 20}
            if self.location_id:
                params['locationId'] = self.location_id
            if pipeline_id:
                params['pipelineId'] = pipeline_id
            if status:
                params['status'] = status
            if contact_id:
                params['contactId'] = contact_id

            resp = self._get('/opportunities/search', params=params)
            if resp.status_code != 200:
                return CRMResult(success=False, message=f'Opportunity search failed: HTTP {resp.status_code}')

            data = resp.json()
            opportunities = data.get('opportunities', [])
            deals = []
            for opp in opportunities:
                deals.append(CRMDeal(
                    id=str(opp.get('id', '')),
                    title=opp.get('name', ''),
                    stage=opp.get('pipelineStageId', ''),
                    value=float(opp.get('monetaryValue', 0) or 0),
                    contact_id=opp.get('contactId', ''),
                    raw_data=opp,
                ))
            return CRMResult(success=True, message=f'Found {len(deals)} opportunity(ies)', data={'deals': deals})
        except Exception as e:
            return CRMResult(success=False, message=f'Opportunity search failed: {str(e)}')


# Auto-register with the connector registry
register_connector('pipereply', PipeReplyConnector)
