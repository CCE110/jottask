"""
CRM Manager - Database CRUD + dispatch layer for CRM connections
Follows the task_manager.py pattern: Supabase client, graceful degradation
"""

import os
from datetime import datetime
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
    # CONNECTION TESTING
    # ========================================

    def test_connection_for_user(self, provider: str, api_key: str,
                                 api_base_url: str = '') -> CRMResult:
        """Instantiate a connector and test the connection.
        Does NOT save anything — just validates credentials.
        """
        try:
            connector = get_connector(
                provider,
                api_key=api_key,
                api_base_url=api_base_url,
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
