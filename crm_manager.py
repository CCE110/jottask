"""
CRM Manager - Database CRUD + dispatch layer for CRM connections
Follows the task_manager.py pattern: Supabase client, graceful degradation
"""

import os
from datetime import datetime, timedelta
from typing import List, Optional
from supabase import create_client, Client
import pytz

from crm_connectors.base import CRMResult
from crm_connectors.registry import get_connector


class CRMManager:
    def __init__(self):
        url = os.getenv('SUPABASE_URL')
        key = os.getenv('SUPABASE_KEY')
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
        self.supabase: Client = create_client(url, key)

    # ========================================
    # CONNECTION CRUD
    # ========================================

    def get_user_connections(self, user_id: str) -> List[dict]:
        """Get all CRM connections for a user."""
        try:
            result = self.supabase.table('crm_connections') \
                .select('*') \
                .eq('user_id', user_id) \
                .order('created_at') \
                .execute()
            return result.data or []
        except Exception as e:
            print(f"CRM: Error fetching connections: {e}")
            return []

    def get_active_connection(self, user_id: str) -> Optional[dict]:
        """Get the first active CRM connection for a user."""
        try:
            result = self.supabase.table('crm_connections') \
                .select('*') \
                .eq('user_id', user_id) \
                .eq('is_active', True) \
                .eq('connection_status', 'connected') \
                .limit(1) \
                .execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"CRM: Error fetching active connection: {e}")
            return None

    def save_connection(self, user_id: str, provider: str, api_key: str = '',
                        api_base_url: str = '', display_name: str = '',
                        connection_status: str = 'connected', is_active: bool = True) -> Optional[dict]:
        """Create or update a CRM connection."""
        try:
            data = {
                'user_id': user_id,
                'provider': provider,
                'api_key': api_key,
                'display_name': display_name or f'{provider.title()} CRM',
                'connection_status': connection_status,
                'is_active': is_active,
                'updated_at': datetime.now(pytz.UTC).isoformat(),
            }
            if api_base_url:
                data['api_base_url'] = api_base_url

            # Check if exists
            existing = self.supabase.table('crm_connections') \
                .select('id') \
                .eq('user_id', user_id) \
                .eq('provider', provider) \
                .execute()

            if existing.data:
                # Update existing
                result = self.supabase.table('crm_connections') \
                    .update(data) \
                    .eq('id', existing.data[0]['id']) \
                    .execute()
            else:
                # Insert new
                result = self.supabase.table('crm_connections') \
                    .insert(data) \
                    .execute()

            return result.data[0] if result.data else None
        except Exception as e:
            print(f"CRM: Error saving connection: {e}")
            return None

    def update_connection_status(self, connection_id: str, status: str, error: str = '') -> bool:
        """Update a connection's status and optionally set last_error."""
        try:
            update_data = {
                'connection_status': status,
                'updated_at': datetime.now(pytz.UTC).isoformat(),
            }
            if error:
                update_data['last_error'] = error
            if status == 'connected':
                update_data['last_error'] = None
                update_data['last_sync_at'] = datetime.now(pytz.UTC).isoformat()

            self.supabase.table('crm_connections') \
                .update(update_data) \
                .eq('id', connection_id) \
                .execute()
            return True
        except Exception as e:
            print(f"CRM: Error updating connection status: {e}")
            return False

    def delete_connection(self, connection_id: str, user_id: str) -> bool:
        """Delete a CRM connection (scoped to user for safety)."""
        try:
            self.supabase.table('crm_connections') \
                .delete() \
                .eq('id', connection_id) \
                .eq('user_id', user_id) \
                .execute()
            return True
        except Exception as e:
            print(f"CRM: Error deleting connection: {e}")
            return False

    # ========================================
    # OAUTH TOKEN MANAGEMENT
    # ========================================

    def refresh_token_if_needed(self, connection: dict) -> dict:
        """Check if an OAuth token is expiring soon and refresh if needed.
        Returns the connection dict (updated if refreshed).
        """
        token_expires_at = connection.get('token_expires_at')
        refresh_token = connection.get('refresh_token')
        if not token_expires_at or not refresh_token:
            return connection

        try:
            if isinstance(token_expires_at, str):
                expires = datetime.fromisoformat(token_expires_at.replace('Z', '+00:00'))
            else:
                expires = token_expires_at

            now = datetime.now(pytz.UTC)
            if expires - now > timedelta(minutes=5):
                return connection  # Token still valid

            # Token expiring soon — refresh it
            provider = connection.get('provider', '')
            print(f"CRM: Refreshing {provider} token for connection {connection['id']}")

            connector = get_connector(
                provider,
                api_key=connection.get('api_key', ''),
                api_base_url=connection.get('api_base_url', ''),
                access_token=connection.get('access_token', ''),
                settings=connection.get('settings') or {},
            )

            if not hasattr(connector, 'refresh_access_token'):
                print(f"CRM: {provider} connector does not support token refresh")
                return connection

            result = connector.refresh_access_token(refresh_token)
            if not result.success:
                print(f"CRM: Token refresh failed: {result.message}")
                self.update_connection_status(connection['id'], 'error', f'Token refresh failed: {result.message}')
                return connection

            # Update database with new tokens
            update_data = {
                'access_token': result.data.get('access_token'),
                'token_expires_at': result.data.get('token_expires_at'),
                'updated_at': datetime.now(pytz.UTC).isoformat(),
            }
            # Some providers return a new refresh token
            if result.data.get('refresh_token'):
                update_data['refresh_token'] = result.data['refresh_token']

            self.supabase.table('crm_connections') \
                .update(update_data) \
                .eq('id', connection['id']) \
                .execute()

            # Update the in-memory connection dict
            connection['access_token'] = update_data['access_token']
            connection['token_expires_at'] = update_data['token_expires_at']
            if 'refresh_token' in update_data:
                connection['refresh_token'] = update_data['refresh_token']

            print(f"CRM: Token refreshed successfully for {provider}")
            return connection

        except Exception as e:
            print(f"CRM: Error refreshing token: {e}")
            return connection

    def save_oauth_connection(self, user_id: str, provider: str,
                              access_token: str, refresh_token: str = '',
                              token_expires_at: str = '',
                              display_name: str = '', settings: dict = None) -> Optional[dict]:
        """Save an OAuth-based CRM connection (from OAuth callback)."""
        try:
            data = {
                'user_id': user_id,
                'provider': provider,
                'access_token': access_token,
                'refresh_token': refresh_token,
                'display_name': display_name or f'{provider.title()} CRM',
                'connection_status': 'connected',
                'is_active': True,
                'updated_at': datetime.now(pytz.UTC).isoformat(),
            }
            if token_expires_at:
                data['token_expires_at'] = token_expires_at
            if settings:
                data['settings'] = settings

            # Check if exists
            existing = self.supabase.table('crm_connections') \
                .select('id') \
                .eq('user_id', user_id) \
                .eq('provider', provider) \
                .execute()

            if existing.data:
                result = self.supabase.table('crm_connections') \
                    .update(data) \
                    .eq('id', existing.data[0]['id']) \
                    .execute()
            else:
                result = self.supabase.table('crm_connections') \
                    .insert(data) \
                    .execute()

            return result.data[0] if result.data else None
        except Exception as e:
            print(f"CRM: Error saving OAuth connection: {e}")
            return None

    # ========================================
    # CONNECTION TESTING
    # ========================================

    def test_connection_for_user(self, provider: str, api_key: str = '',
                                 api_base_url: str = '', access_token: str = '',
                                 settings: dict = None) -> CRMResult:
        """Instantiate a connector and test the connection.
        Does NOT save anything — just validates credentials.
        """
        try:
            connector = get_connector(
                provider,
                api_key=api_key,
                api_base_url=api_base_url,
                access_token=access_token,
                settings=settings or {},
            )
            return connector.test_connection()
        except ValueError as e:
            return CRMResult(success=False, message=str(e))
        except Exception as e:
            return CRMResult(success=False, message=f'Connection test failed: {str(e)}')

    # ========================================
    # CRM DISPATCH (main entry point)
    # ========================================

    def execute_crm_update(self, user_id: str, customer_name: str,
                           crm_notes: str, customer_email: str = '') -> CRMResult:
        """Main entry point: find contact → add note.
        Returns CRMResult. Caller decides what to do on failure.
        """
        connection = self.get_active_connection(user_id)
        if not connection:
            return CRMResult(success=False, message='No active CRM connection')

        # Refresh OAuth token if needed
        connection = self.refresh_token_if_needed(connection)

        try:
            connector = get_connector(
                connection['provider'],
                api_key=connection.get('api_key', ''),
                api_base_url=connection.get('api_base_url', ''),
                access_token=connection.get('access_token', ''),
                settings=connection.get('settings') or {},
            )

            # Step 1: Find the contact
            find_result = connector.find_contact(name=customer_name, email=customer_email)
            if not find_result.success or not find_result.contacts:
                # Contact not found — not a failure, just can't sync
                return CRMResult(
                    success=False,
                    message=f"Contact '{customer_name}' not found in {connection['provider'].title()}"
                )

            contact = find_result.contacts[0]

            # Step 2: Add the note
            note_result = connector.add_note(contact.id, crm_notes)
            if note_result.success:
                # Update last_sync_at
                self.update_connection_status(connection['id'], 'connected')
                return CRMResult(
                    success=True,
                    message=f"Note added to {contact.name} in {connection['provider'].title()}",
                    contact=contact,
                )
            else:
                self.update_connection_status(connection['id'], 'error', note_result.message)
                return note_result

        except Exception as e:
            error_msg = f"CRM update failed: {str(e)}"
            print(f"CRM: {error_msg}")
            if connection:
                self.update_connection_status(connection['id'], 'error', error_msg)
            return CRMResult(success=False, message=error_msg)
