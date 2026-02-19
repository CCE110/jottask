"""Tests for task_manager.py helpers."""

from unittest.mock import MagicMock, patch
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_task_manager_instantiation():
    """TaskManager should instantiate with mocked Supabase."""
    mock_client = MagicMock()
    # Mock load_project_statuses to return empty list
    mock_client.table.return_value.select.return_value.order.return_value.execute.return_value.data = []

    with patch('supabase.create_client', return_value=mock_client):
        # Need to reload since module may be cached
        if 'task_manager' in sys.modules:
            del sys.modules['task_manager']
        from task_manager import TaskManager
        tm = TaskManager()
        assert tm.supabase is mock_client
        assert isinstance(tm.statuses, dict)
