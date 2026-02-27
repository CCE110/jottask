"""
Jottask Chat Blueprint — AI-powered chat interface with Claude tool-use
Routes, SSE streaming, conversation management
"""

import os
import json
from datetime import datetime, date
from flask import Blueprint, request, session, jsonify, render_template, Response, stream_with_context
from anthropic import Anthropic
from supabase import create_client, Client
import pytz

from auth import login_required
from chat_tools import TOOL_HANDLERS

chat_bp = Blueprint('chat', __name__)

# Clients
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
claude = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

AEST = pytz.timezone('Australia/Brisbane')
CHAT_MODEL = "claude-sonnet-4-20250514"


# ========================================
# TOOL DEFINITIONS (Claude tool schemas)
# ========================================

def get_tool_definitions():
    return [
        {
            "name": "create_task",
            "description": "Create a new task. Use this when the user wants to add a task, reminder, or to-do item.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title/description"},
                    "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
                    "due_time": {"type": "string", "description": "Due time in HH:MM format (24hr)"},
                    "priority": {"type": "string", "enum": ["urgent", "high", "medium", "low"], "description": "Task priority"},
                    "client_name": {"type": "string", "description": "Client name if task relates to a client"},
                    "description": {"type": "string", "description": "Additional details about the task"},
                },
                "required": ["title"]
            }
        },
        {
            "name": "list_tasks",
            "description": "List tasks with optional filters. Use for browsing tasks by status or date range.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["pending", "completed", "all"], "description": "Filter by status (default: pending)"},
                    "date_filter": {"type": "string", "enum": ["today", "tomorrow", "this_week", "overdue", "all"], "description": "Filter by date range"},
                },
            }
        },
        {
            "name": "complete_task",
            "description": "Mark a task as completed/done. Can find task by ID or by searching the title.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task UUID (if known)"},
                    "task_title_search": {"type": "string", "description": "Search term to find the task by title"},
                },
            }
        },
        {
            "name": "delay_task",
            "description": "Reschedule/delay/push a task. Can delay by hours/days or set a specific new date/time.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task UUID (if known)"},
                    "task_title_search": {"type": "string", "description": "Search term to find the task by title"},
                    "hours": {"type": "integer", "description": "Delay by N hours"},
                    "days": {"type": "integer", "description": "Delay by N days"},
                    "new_date": {"type": "string", "description": "New due date (YYYY-MM-DD)"},
                    "new_time": {"type": "string", "description": "New due time (HH:MM, 24hr)"},
                },
            }
        },
        {
            "name": "get_overdue_tasks",
            "description": "Get all pending tasks that are past their due date.",
            "input_schema": {
                "type": "object",
                "properties": {},
            }
        },
        {
            "name": "get_todays_tasks",
            "description": "Get all pending tasks due today.",
            "input_schema": {
                "type": "object",
                "properties": {},
            }
        },
        {
            "name": "search_tasks",
            "description": "Search tasks by keyword across title, description, and client name.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword"},
                    "include_completed": {"type": "boolean", "description": "Include completed tasks in results (default: false)"},
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_task_details",
            "description": "Get full details of a specific task including notes and checklist items.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task UUID"},
                },
                "required": ["task_id"]
            }
        },
        {
            "name": "add_note_to_task",
            "description": "Add a note/comment to an existing task. Can find task by ID or title search.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task UUID (if known)"},
                    "task_title_search": {"type": "string", "description": "Search term to find the task by title"},
                    "content": {"type": "string", "description": "Note content to add"},
                },
                "required": ["content"]
            }
        },
    ]


# ========================================
# SYSTEM PROMPT
# ========================================

def build_system_prompt(user_id):
    """Build a dynamic system prompt with user context and live stats."""
    now = datetime.now(AEST)
    today = date.today()

    # Get user info
    user_name = session.get('user_name', 'there')
    company = ''
    timezone = session.get('timezone', 'Australia/Brisbane')

    try:
        user_result = supabase.table('users') \
            .select('full_name, company_name, timezone') \
            .eq('id', user_id).single().execute()
        if user_result.data:
            user_name = user_result.data.get('full_name') or user_name
            company = user_result.data.get('company_name') or ''
            timezone = user_result.data.get('timezone') or timezone
    except Exception:
        pass

    # Get live task stats
    overdue_count = 0
    today_count = 0
    pending_count = 0

    try:
        overdue = supabase.table('tasks').select('id', count='exact') \
            .eq('user_id', user_id).eq('status', 'pending') \
            .lt('due_date', today.isoformat()).execute()
        overdue_count = overdue.count or 0
    except Exception:
        pass

    try:
        todays = supabase.table('tasks').select('id', count='exact') \
            .eq('user_id', user_id).eq('status', 'pending') \
            .eq('due_date', today.isoformat()).execute()
        today_count = todays.count or 0
    except Exception:
        pass

    try:
        pending = supabase.table('tasks').select('id', count='exact') \
            .eq('user_id', user_id).eq('status', 'pending').execute()
        pending_count = pending.count or 0
    except Exception:
        pass

    company_line = f" at {company}" if company else ""

    return f"""You are Jottask AI, a task management assistant for {user_name}{company_line}.
Today: {now.strftime('%A, %d %B %Y')}. Time: {now.strftime('%I:%M %p')} {timezone}.

Current workload: {overdue_count} overdue, {today_count} due today, {pending_count} total pending.

You help manage tasks through conversation. Be concise and helpful. When creating tasks, confirm what you created with the key details. For relative dates ("tomorrow", "next Monday"), calculate the actual date from today ({today.isoformat()}). If a request is ambiguous (multiple tasks match), ask which task they mean. Stay focused on task management.

When listing tasks, format them clearly. Use the tools provided — don't make up task data."""


# ========================================
# TOOL EXECUTION
# ========================================

def execute_tool(name, tool_input, user_id):
    """Dispatch a tool call to its handler."""
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return {'success': False, 'error': f'Unknown tool: {name}'}
    return handler(tool_input, user_id, supabase)


# ========================================
# CONVERSATION HELPERS
# ========================================

def load_conversation_messages(conversation_id, user_id, limit=20):
    """Load recent messages for a conversation (for Claude context)."""
    try:
        # Verify conversation belongs to user
        conv = supabase.table('chat_conversations') \
            .select('id') \
            .eq('id', conversation_id) \
            .eq('user_id', user_id) \
            .execute()

        if not conv.data:
            return []

        result = supabase.table('chat_messages') \
            .select('role, content, tool_calls, tool_results') \
            .eq('conversation_id', conversation_id) \
            .order('created_at') \
            .limit(limit) \
            .execute()

        # Convert DB messages to Claude API format
        messages = []
        for msg in result.data:
            if msg['role'] == 'user':
                messages.append({'role': 'user', 'content': msg['content']})
            elif msg['role'] == 'assistant':
                # Reconstruct content blocks
                content_blocks = []
                if msg['content']:
                    content_blocks.append({'type': 'text', 'text': msg['content']})
                if msg.get('tool_calls'):
                    for tc in msg['tool_calls']:
                        content_blocks.append({
                            'type': 'tool_use',
                            'id': tc['id'],
                            'name': tc['name'],
                            'input': tc['input'],
                        })
                if content_blocks:
                    messages.append({'role': 'assistant', 'content': content_blocks})
            elif msg['role'] == 'tool_result':
                # Tool results become user messages with tool_result blocks
                if msg.get('tool_results'):
                    content_blocks = []
                    for tr in msg['tool_results']:
                        content_blocks.append({
                            'type': 'tool_result',
                            'tool_use_id': tr['tool_use_id'],
                            'content': json.dumps(tr['result']),
                        })
                    messages.append({'role': 'user', 'content': content_blocks})

        return messages
    except Exception as e:
        print(f"Error loading messages: {e}")
        return []


def save_message(conversation_id, role, content=None, tool_calls=None, tool_results=None):
    """Save a message to the database."""
    try:
        msg_data = {
            'conversation_id': conversation_id,
            'role': role,
            'content': content,
            'tool_calls': tool_calls,
            'tool_results': tool_results,
        }
        result = supabase.table('chat_messages').insert(msg_data).execute()

        # Update conversation's updated_at
        supabase.table('chat_conversations') \
            .update({'updated_at': datetime.now(AEST).isoformat()}) \
            .eq('id', conversation_id) \
            .execute()

        return result.data[0] if result.data else None
    except Exception as e:
        print(f"Error saving message: {e}")
        return None


def auto_title_conversation(conversation_id, user_message):
    """Generate a short title from the first user message."""
    try:
        # Simple: use first ~50 chars of the message
        title = user_message[:50].strip()
        if len(user_message) > 50:
            title = title.rsplit(' ', 1)[0] + '...'

        supabase.table('chat_conversations') \
            .update({'title': title}) \
            .eq('id', conversation_id) \
            .execute()
        return title
    except Exception:
        return None


# ========================================
# STREAMING RESPONSE GENERATOR
# ========================================

def stream_chat_response(conversation_id, user_message, user_id):
    """
    Generator that streams the Claude response as SSE events.
    Handles tool-use loops: Claude calls tool → we execute → send result back → Claude continues.
    """
    # Save user message
    save_message(conversation_id, 'user', content=user_message)

    # Auto-title on first message
    msg_count = supabase.table('chat_messages') \
        .select('id', count='exact') \
        .eq('conversation_id', conversation_id) \
        .execute()
    if msg_count.count and msg_count.count <= 1:
        title = auto_title_conversation(conversation_id, user_message)
        if title:
            yield f"data: {json.dumps({'type': 'title_update', 'title': title})}\n\n"

    # Load conversation history
    history = load_conversation_messages(conversation_id, user_id)

    # Build system prompt
    system_prompt = build_system_prompt(user_id)
    tools = get_tool_definitions()

    # Messages for the Claude API call
    api_messages = history.copy()

    # Start the tool-use loop (max 5 iterations to prevent runaway)
    for _iteration in range(5):
        # Call Claude with streaming
        full_text = ""
        tool_uses = []
        current_tool_input_json = ""
        current_tool = None

        with claude.messages.stream(
            model=CHAT_MODEL,
            max_tokens=2048,
            system=system_prompt,
            tools=tools,
            messages=api_messages,
        ) as stream:
            for event in stream:
                if event.type == 'content_block_start':
                    if event.content_block.type == 'text':
                        pass  # Text will come in deltas
                    elif event.content_block.type == 'tool_use':
                        current_tool = {
                            'id': event.content_block.id,
                            'name': event.content_block.name,
                            'input': {},
                        }
                        current_tool_input_json = ""
                        yield f"data: {json.dumps({'type': 'tool_start', 'tool': event.content_block.name})}\n\n"

                elif event.type == 'content_block_delta':
                    if hasattr(event.delta, 'text'):
                        full_text += event.delta.text
                        yield f"data: {json.dumps({'type': 'text', 'content': event.delta.text})}\n\n"
                    elif hasattr(event.delta, 'partial_json'):
                        current_tool_input_json += event.delta.partial_json

                elif event.type == 'content_block_stop':
                    if current_tool:
                        # Parse the accumulated JSON input
                        try:
                            current_tool['input'] = json.loads(current_tool_input_json) if current_tool_input_json else {}
                        except json.JSONDecodeError:
                            current_tool['input'] = {}
                        tool_uses.append(current_tool)
                        current_tool = None
                        current_tool_input_json = ""

        # If no tool calls, save and finish
        if not tool_uses:
            save_message(conversation_id, 'assistant', content=full_text)
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        # Execute tools and continue the loop
        # First, save the assistant message with tool calls
        tool_calls_data = [{'id': t['id'], 'name': t['name'], 'input': t['input']} for t in tool_uses]

        # Build assistant content blocks for API
        assistant_content = []
        if full_text:
            assistant_content.append({'type': 'text', 'text': full_text})
        for t in tool_uses:
            assistant_content.append({
                'type': 'tool_use',
                'id': t['id'],
                'name': t['name'],
                'input': t['input'],
            })

        save_message(conversation_id, 'assistant', content=full_text or None, tool_calls=tool_calls_data)

        # Execute each tool and collect results
        tool_result_blocks = []
        tool_results_data = []

        for tool_use in tool_uses:
            result = execute_tool(tool_use['name'], tool_use['input'], user_id)
            tool_result_blocks.append({
                'type': 'tool_result',
                'tool_use_id': tool_use['id'],
                'content': json.dumps(result),
            })
            tool_results_data.append({
                'tool_use_id': tool_use['id'],
                'tool_name': tool_use['name'],
                'result': result,
            })

            # Send tool result to frontend
            yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool_use['name'], 'result': result})}\n\n"

        # Save tool results message
        save_message(conversation_id, 'tool_result', tool_results=tool_results_data)

        # Add to API messages for next iteration
        api_messages.append({'role': 'assistant', 'content': assistant_content})
        api_messages.append({'role': 'user', 'content': tool_result_blocks})

    # If we exhaust iterations, finish
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


# ========================================
# ROUTES
# ========================================

@chat_bp.route('/chat')
@login_required
def chat_page():
    """Render the full-page chat interface."""
    return render_template('chat.html')


@chat_bp.route('/chat/conversations', methods=['GET'])
@login_required
def list_conversations():
    """List user's conversations for the sidebar."""
    user_id = session['user_id']
    try:
        result = supabase.table('chat_conversations') \
            .select('id, title, updated_at') \
            .eq('user_id', user_id) \
            .order('updated_at', desc=True) \
            .limit(50) \
            .execute()
        return jsonify({'conversations': result.data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@chat_bp.route('/chat/conversations', methods=['POST'])
@login_required
def create_conversation():
    """Create a new conversation."""
    user_id = session['user_id']
    try:
        result = supabase.table('chat_conversations').insert({
            'user_id': user_id,
            'title': 'New Chat',
        }).execute()
        if result.data:
            return jsonify({'conversation': result.data[0]})
        return jsonify({'error': 'Failed to create conversation'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@chat_bp.route('/chat/conversations/<conversation_id>', methods=['DELETE'])
@login_required
def delete_conversation(conversation_id):
    """Delete a conversation and its messages."""
    user_id = session['user_id']
    try:
        # Messages cascade-delete via FK
        supabase.table('chat_conversations') \
            .delete() \
            .eq('id', conversation_id) \
            .eq('user_id', user_id) \
            .execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@chat_bp.route('/chat/conversations/<conversation_id>/messages', methods=['GET'])
@login_required
def get_messages(conversation_id):
    """Load messages for a conversation (for displaying history)."""
    user_id = session['user_id']
    try:
        # Verify ownership
        conv = supabase.table('chat_conversations') \
            .select('id').eq('id', conversation_id).eq('user_id', user_id).execute()
        if not conv.data:
            return jsonify({'error': 'Conversation not found'}), 404

        result = supabase.table('chat_messages') \
            .select('id, role, content, tool_calls, tool_results, created_at') \
            .eq('conversation_id', conversation_id) \
            .order('created_at') \
            .limit(100) \
            .execute()

        return jsonify({'messages': result.data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@chat_bp.route('/chat/conversations/<conversation_id>/messages', methods=['POST'])
@login_required
def send_message(conversation_id):
    """Send a message and stream the AI response via SSE."""
    user_id = session['user_id']
    data = request.get_json()
    user_message = (data or {}).get('message', '').strip()

    if not user_message:
        return jsonify({'error': 'Message is required'}), 400

    # Verify conversation ownership
    try:
        conv = supabase.table('chat_conversations') \
            .select('id').eq('id', conversation_id).eq('user_id', user_id).execute()
        if not conv.data:
            return jsonify({'error': 'Conversation not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    def generate():
        try:
            yield from stream_chat_response(conversation_id, user_message, user_id)
        except Exception as e:
            print(f"Stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    response = Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )
    return response
