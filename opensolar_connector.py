"""
OpenSolar Connector — API integration for solar quoting platform

Auth: POST credentials to /api-token-auth/ -> Bearer token
Base URL: https://api.opensolar.com
Token lasts 7 days, or set is_machine_user=true for permanent token.

For SaaS subscribers: they enter their OpenSolar email + password in Jottask settings.
Jottask authenticates on their behalf and caches the token.
"""

import os
import requests
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

import pytz

REQUEST_TIMEOUT = 20
BASE_URL = 'https://api.opensolar.com'


@dataclass
class OpenSolarProject:
    """Standardised project representation"""
    id: str = ''
    title: str = ''
    address: str = ''
    stage: str = ''
    org_id: str = ''
    contacts: list = field(default_factory=list)
    systems: list = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)


@dataclass
class OpenSolarResult:
    """Result wrapper for all OpenSolar operations"""
    success: bool
    message: str = ''
    data: Optional[dict] = None
    project: Optional[OpenSolarProject] = None
    projects: list = field(default_factory=list)


class OpenSolarConnector:
    """OpenSolar API connector using credential-based authentication.

    Usage:
        connector = OpenSolarConnector(email='...', password='...')
        result = connector.authenticate()
        if result.success:
            projects = connector.list_projects()
    """

    def __init__(self, email: str = '', password: str = '',
                 token: str = '', org_id: str = ''):
        self.email = email
        self.password = password
        self.token = token
        self.org_id = org_id
        self.user_data = {}
        self.token_expires_at = None

    def _headers(self) -> dict:
        h = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        if self.token:
            h['Authorization'] = f'Bearer {self.token}'
        return h

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

    # ======================================================
    # Authentication
    # ======================================================

    def authenticate(self) -> OpenSolarResult:
        """Login with email/password to get a Bearer token.
        Also extracts org_id from the user profile for subsequent calls.
        """
        if not self.email or not self.password:
            return OpenSolarResult(success=False, message='Email and password required')

        try:
            resp = requests.post(
                f'{BASE_URL}/api-token-auth/',
                json={'username': self.email, 'password': self.password},
                headers={'Content-Type': 'application/json'},
                timeout=REQUEST_TIMEOUT,
            )

            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get('token', '')
                self.user_data = data
                self.token_expires_at = datetime.now(pytz.UTC) + timedelta(days=7)

                # Extract org_id from user data
                org = data.get('org', data.get('org_id', ''))
                if isinstance(org, dict):
                    org = org.get('id', org.get('org_id', ''))
                if org:
                    self.org_id = str(org)

                # Try to get org_id from orgs list if not in auth response
                if not self.org_id:
                    self._discover_org_id()

                return OpenSolarResult(
                    success=True,
                    message=f'Authenticated as {self.email} (org: {self.org_id})',
                    data=data,
                )
            elif resp.status_code == 400:
                return OpenSolarResult(success=False, message='Invalid credentials — check email and password')
            else:
                return OpenSolarResult(
                    success=False,
                    message=f'Login failed: HTTP {resp.status_code}: {resp.text[:300]}',
                )
        except requests.ConnectionError:
            return OpenSolarResult(success=False, message='Cannot reach OpenSolar API')
        except requests.Timeout:
            return OpenSolarResult(success=False, message='OpenSolar login timed out')
        except Exception as e:
            return OpenSolarResult(success=False, message=f'Login error: {str(e)}')

    def _discover_org_id(self):
        """Try to find org_id from the user's org membership."""
        try:
            resp = self._get('/api/orgs/')
            if resp.status_code == 200:
                data = resp.json()
                orgs = data if isinstance(data, list) else data.get('results', data.get('data', []))
                if orgs:
                    self.org_id = str(orgs[0].get('id', orgs[0].get('org_id', '')))
        except Exception:
            pass

    def ensure_authenticated(self) -> OpenSolarResult:
        """Re-authenticate if token is missing or expired."""
        if self.token and self.token_expires_at:
            if datetime.now(pytz.UTC) < self.token_expires_at - timedelta(hours=1):
                return OpenSolarResult(success=True, message='Token still valid')

        return self.authenticate()

    # ======================================================
    # Test Connection
    # ======================================================

    def test_connection(self) -> OpenSolarResult:
        """Authenticate and verify access by listing one project."""
        auth = self.ensure_authenticated()
        if not auth.success:
            return auth

        try:
            resp = self._get(f'/api/orgs/{self.org_id}/projects/', params={'limit': 1})
            if resp.status_code == 200:
                data = resp.json()
                count = data.get('count', len(data.get('results', [])))
                return OpenSolarResult(
                    success=True,
                    message=f'Connected to OpenSolar (org: {self.org_id}, {count} projects)',
                    data=data,
                )
            else:
                return OpenSolarResult(
                    success=False,
                    message=f'OpenSolar returned HTTP {resp.status_code}: {resp.text[:200]}',
                )
        except Exception as e:
            return OpenSolarResult(success=False, message=f'Test failed: {str(e)}')

    # ======================================================
    # Projects
    # ======================================================

    def list_projects(self, limit: int = 20, page: int = 1,
                      search: str = '') -> OpenSolarResult:
        """List projects for the org, with optional search."""
        auth = self.ensure_authenticated()
        if not auth.success:
            return auth

        try:
            params = {'limit': limit, 'page': page, 'fieldset': 'list'}
            if search:
                params['search'] = search

            resp = self._get(f'/api/orgs/{self.org_id}/projects/', params=params)
            if resp.status_code != 200:
                return OpenSolarResult(success=False, message=f'List projects failed: HTTP {resp.status_code}')

            data = resp.json()
            items = data.get('results', data) if isinstance(data, dict) else data
            if not isinstance(items, list):
                items = [items] if items else []

            projects = []
            for item in items:
                projects.append(self._parse_project(item))

            return OpenSolarResult(
                success=True,
                message=f'Found {len(projects)} project(s)',
                projects=projects,
                data=data,
            )
        except Exception as e:
            return OpenSolarResult(success=False, message=f'List projects failed: {str(e)}')

    def get_project(self, project_id: str) -> OpenSolarResult:
        """Get full details for a specific project."""
        auth = self.ensure_authenticated()
        if not auth.success:
            return auth

        try:
            resp = self._get(f'/api/orgs/{self.org_id}/projects/{project_id}/')
            if resp.status_code != 200:
                return OpenSolarResult(success=False, message=f'Get project failed: HTTP {resp.status_code}')

            item = resp.json()
            project = self._parse_project(item)
            return OpenSolarResult(success=True, message='Project retrieved', project=project, data=item)
        except Exception as e:
            return OpenSolarResult(success=False, message=f'Get project failed: {str(e)}')

    def create_project(self, address: str, first_name: str = '', last_name: str = '',
                       email: str = '', phone: str = '', notes: str = '',
                       is_residential: bool = True, lead_source: str = '') -> OpenSolarResult:
        """Create a new project with contact in OpenSolar."""
        auth = self.ensure_authenticated()
        if not auth.success:
            return auth

        try:
            payload = {
                'address': address,
                'is_residential': is_residential,
            }
            if notes:
                payload['notes'] = notes
            if lead_source:
                payload['lead_source'] = lead_source

            # Attach contact if provided
            if first_name or email:
                contact = {}
                if first_name:
                    contact['first_name'] = first_name
                if last_name:
                    contact['last_name'] = last_name
                if email:
                    contact['email'] = email
                if phone:
                    contact['phone'] = phone
                payload['contacts_new'] = [contact]

            resp = self._post(f'/api/orgs/{self.org_id}/projects/', json_data=payload)
            if resp.status_code in (200, 201):
                item = resp.json()
                project = self._parse_project(item)
                return OpenSolarResult(success=True, message=f'Project created: {project.id}', project=project, data=item)
            else:
                return OpenSolarResult(success=False, message=f'Create project failed: HTTP {resp.status_code}: {resp.text[:300]}')
        except Exception as e:
            return OpenSolarResult(success=False, message=f'Create project failed: {str(e)}')

    def update_project(self, project_id: str, updates: dict) -> OpenSolarResult:
        """Update an existing project (PATCH)."""
        auth = self.ensure_authenticated()
        if not auth.success:
            return auth

        try:
            resp = self._patch(f'/api/orgs/{self.org_id}/projects/{project_id}/', json_data=updates)
            if resp.status_code in (200, 201):
                item = resp.json()
                project = self._parse_project(item)
                return OpenSolarResult(success=True, message='Project updated', project=project, data=item)
            else:
                return OpenSolarResult(success=False, message=f'Update project failed: HTTP {resp.status_code}: {resp.text[:200]}')
        except Exception as e:
            return OpenSolarResult(success=False, message=f'Update project failed: {str(e)}')

    # ======================================================
    # Systems / Proposals
    # ======================================================

    def list_systems(self, project_id: str = '') -> OpenSolarResult:
        """List systems/designs, optionally filtered by project."""
        auth = self.ensure_authenticated()
        if not auth.success:
            return auth

        try:
            params = {'fieldset': 'list'}
            if project_id:
                params['project'] = project_id

            resp = self._get(f'/api/orgs/{self.org_id}/systems/', params=params)
            if resp.status_code != 200:
                return OpenSolarResult(success=False, message=f'List systems failed: HTTP {resp.status_code}')

            data = resp.json()
            return OpenSolarResult(success=True, message='Systems retrieved', data=data)
        except Exception as e:
            return OpenSolarResult(success=False, message=f'List systems failed: {str(e)}')

    def get_system(self, system_id: str) -> OpenSolarResult:
        """Get full system/design details including equipment."""
        auth = self.ensure_authenticated()
        if not auth.success:
            return auth

        try:
            resp = self._get(f'/api/orgs/{self.org_id}/systems/{system_id}/')
            if resp.status_code != 200:
                return OpenSolarResult(success=False, message=f'Get system failed: HTTP {resp.status_code}')

            data = resp.json()
            return OpenSolarResult(success=True, message='System retrieved', data=data)
        except Exception as e:
            return OpenSolarResult(success=False, message=f'Get system failed: {str(e)}')

    # ======================================================
    # Install Order Helper
    # ======================================================

    def get_install_order_data(self, project_id: str) -> OpenSolarResult:
        """Get all data needed for an install order: project + systems + contacts.
        This replaces the browser-automated /install-order shortcut.
        """
        project_result = self.get_project(project_id)
        if not project_result.success:
            return project_result

        project = project_result.project
        systems_result = self.list_systems(project_id=project_id)

        install_data = {
            'project': project_result.data,
            'project_id': project.id,
            'address': project.address,
            'title': project.title,
            'contacts': project.contacts,
            'systems': systems_result.data if systems_result.success else {},
        }

        return OpenSolarResult(
            success=True,
            message=f'Install order data for project {project_id}',
            project=project,
            data=install_data,
        )

    # ======================================================
    # Internal helpers
    # ======================================================

    def _parse_project(self, item: dict) -> OpenSolarProject:
        """Parse an OpenSolar project API response into OpenSolarProject."""
        # Handle contacts
        contacts = item.get('contacts_data', item.get('contacts', []))
        if not isinstance(contacts, list):
            contacts = []

        # Handle stage/workflow
        workflow = item.get('workflow', {})
        stage = ''
        if isinstance(workflow, dict):
            stage = workflow.get('active_stage_id', '')
        if not stage:
            stage = item.get('stage', '')

        return OpenSolarProject(
            id=str(item.get('id', '')),
            title=item.get('title', item.get('address', '')),
            address=item.get('address', ''),
            stage=str(stage),
            org_id=str(item.get('org_id', self.org_id)),
            contacts=contacts,
            systems=item.get('systems', []),
            raw_data=item,
        )
