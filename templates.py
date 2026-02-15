"""
Jottask HTML Templates
Separated for cleaner code organization
"""

# ============================================
# TASK EDIT PAGE
# ============================================

TASK_EDIT_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<nav class="nav">
    <a href="{{ url_for('dashboard') }}" class="nav-brand">
        <svg viewBox="0 0 512 512" width="32" height="32">
            <defs>
                <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" style="stop-color:#8B5CF6" />
                    <stop offset="100%" style="stop-color:#6366F1" />
                </linearGradient>
            </defs>
            <rect width="512" height="512" rx="96" fill="white"/>
            <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
            <circle cx="310" cy="350" r="70" fill="#10B981"/>
            <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Jottask
    </a>
    <div class="nav-user">
        <a href="{{ url_for('dashboard') }}" class="btn btn-secondary btn-sm">← Back to Tasks</a>
    </div>
</nav>

<main class="main" style="max-width: 700px;">
    <div class="card">
        <div class="card-header">
            <h2 class="card-title">Edit Task</h2>
            {% if task.status == 'completed' %}
            <span class="status-badge" style="background: var(--success); color: white;">Completed</span>
            {% endif %}
        </div>

        <form method="POST" class="card-body">
            <div class="form-group">
                <label class="form-label">Title</label>
                <input type="text" name="title" class="form-input" value="{{ task.title }}" required>
            </div>

            <div class="form-group">
                <label class="form-label">Description</label>
                <textarea name="description" class="form-input" rows="4" placeholder="Add details about this task...">{{ task.description or '' }}</textarea>
            </div>

            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                <div class="form-group">
                    <label class="form-label">Due Date</label>
                    <input type="date" name="due_date" class="form-input" value="{{ task.due_date }}">
                </div>
                <div class="form-group">
                    <label class="form-label">Due Time</label>
                    <input type="time" name="due_time" class="form-input" value="{{ task.due_time[:5] if task.due_time else '09:00' }}">
                </div>
            </div>

            <div class="form-group">
                <label class="form-label">Priority</label>
                <select name="priority" class="form-input">
                    <option value="low" {% if task.priority == 'low' %}selected{% endif %}>Low</option>
                    <option value="medium" {% if task.priority == 'medium' %}selected{% endif %}>Medium</option>
                    <option value="high" {% if task.priority == 'high' %}selected{% endif %}>High</option>
                    <option value="urgent" {% if task.priority == 'urgent' %}selected{% endif %}>Urgent</option>
                </select>
            </div>

            <div class="form-group">
                <label class="form-label">Status</label>
                <input type="hidden" name="status" id="status-input" value="{{ task.status }}">
                <div style="display: flex; gap: 8px;">
                    <button type="button" class="status-btn" data-status="pending" onclick="setStatus('pending')"
                        style="flex: 1; padding: 10px 16px; border-radius: 8px; font-weight: 600; font-size: 14px; cursor: pointer; border: 2px solid {% if task.status == 'pending' %}#F59E0B{% else %}var(--gray-200){% endif %}; background: {% if task.status == 'pending' %}#FEF3C7{% else %}white{% endif %}; color: {% if task.status == 'pending' %}#92400E{% else %}var(--gray-500){% endif %};">
                        Pending
                    </button>
                    <button type="button" class="status-btn" data-status="ongoing" onclick="setStatus('ongoing')"
                        style="flex: 1; padding: 10px 16px; border-radius: 8px; font-weight: 600; font-size: 14px; cursor: pointer; border: 2px solid {% if task.status == 'ongoing' %}var(--primary){% else %}var(--gray-200){% endif %}; background: {% if task.status == 'ongoing' %}#E0E7FF{% else %}white{% endif %}; color: {% if task.status == 'ongoing' %}#3730A3{% else %}var(--gray-500){% endif %};">
                        Ongoing
                    </button>
                    <button type="button" class="status-btn" data-status="completed" onclick="setStatus('completed')"
                        style="flex: 1; padding: 10px 16px; border-radius: 8px; font-weight: 600; font-size: 14px; cursor: pointer; border: 2px solid {% if task.status == 'completed' %}var(--success){% else %}var(--gray-200){% endif %}; background: {% if task.status == 'completed' %}#D1FAE5{% else %}white{% endif %}; color: {% if task.status == 'completed' %}#065F46{% else %}var(--gray-500){% endif %};">
                        Completed
                    </button>
                </div>
            </div>

            <hr style="margin: 24px 0; border: none; border-top: 1px solid var(--gray-200);">

            <h3 style="font-size: 14px; color: var(--gray-500); margin-bottom: 16px;">CLIENT INFORMATION</h3>

            <div class="form-group">
                <label class="form-label">Client Name</label>
                <input type="text" name="client_name" class="form-input" value="{{ task.client_name or '' }}" placeholder="John Smith">
            </div>

            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                <div class="form-group">
                    <label class="form-label">Client Email</label>
                    <input type="email" name="client_email" class="form-input" value="{{ task.client_email or '' }}" placeholder="john@example.com">
                </div>
                <div class="form-group">
                    <label class="form-label">Client Phone</label>
                    <input type="tel" name="client_phone" class="form-input" value="{{ task.client_phone or '' }}" placeholder="0400 000 000">
                </div>
            </div>

            <div class="form-group">
                <label class="form-label">Project Name</label>
                <input type="text" name="project_name" class="form-input" value="{{ task.project_name or '' }}" placeholder="Website Redesign">
            </div>

            <hr style="margin: 24px 0; border: none; border-top: 1px solid var(--gray-200);">

            <h3 style="font-size: 14px; color: var(--gray-500); margin-bottom: 16px;">QUICK RESCHEDULE</h3>

            <div class="delay-buttons" style="margin-bottom: 24px;">
                <button type="button" class="delay-btn" onclick="delayTask('{{ task.id }}', 1, 0)">+1 Hour</button>
                <button type="button" class="delay-btn" onclick="delayTask('{{ task.id }}', 3, 0)">+3 Hours</button>
                <button type="button" class="delay-btn" onclick="delayTask('{{ task.id }}', 0, 1)">+1 Day</button>
                <button type="button" class="delay-btn" onclick="delayTask('{{ task.id }}', 0, 7)">+1 Week</button>
            </div>

            <div style="display: flex; gap: 12px;">
                <button type="submit" class="btn btn-primary" style="flex: 1;">Save Changes</button>
                <a href="{{ url_for('task_detail', task_id=task.id) }}" class="btn btn-secondary">View Details</a>
            </div>
        </form>
    </div>

    <!-- Checklist Card -->
    <div class="card" style="margin-top: 20px;">
        <div class="card-header">
            <h3 class="card-title">Checklist</h3>
            <span style="color: var(--gray-500); font-size: 14px;" id="checklist-counter">
                {{ checklist|selectattr('is_completed')|list|length }}/{{ checklist|length }}
            </span>
        </div>
        <div class="card-body" style="padding: 12px 20px;">
            {% if checklist %}
            <div style="display: flex; flex-direction: column; gap: 4px; margin-bottom: 16px;">
                {% for item in checklist %}
                <label style="display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: 8px; cursor: pointer; transition: background 0.15s; {% if item.is_completed %}opacity: 0.6;{% endif %}"
                       onmouseover="this.style.background='var(--gray-50)'" onmouseout="this.style.background='transparent'">
                    <input type="checkbox" {% if item.is_completed %}checked{% endif %}
                           onchange="toggleChecklistItem('{{ task.id }}', '{{ item.id }}', this.checked)"
                           style="width: 20px; height: 20px; accent-color: var(--success); cursor: pointer;">
                    <span style="font-size: 14px; {% if item.is_completed %}text-decoration: line-through; color: var(--gray-500);{% endif %}">{{ item.item_text }}</span>
                </label>
                {% endfor %}
            </div>
            {% else %}
            <p style="color: var(--gray-500); text-align: center; padding: 16px 0; font-size: 14px;">No checklist items yet</p>
            {% endif %}

            <form method="POST" action="{{ url_for('add_checklist_item', task_id=task.id) }}" style="display: flex; gap: 8px;">
                <input type="hidden" name="redirect_to" value="edit">
                <input type="text" name="item_text" class="form-input" placeholder="Add a checklist item..." required style="flex: 1;">
                <button type="submit" class="btn btn-primary btn-sm">Add</button>
            </form>
        </div>
    </div>

    {% if task.status == 'pending' %}
    <div style="text-align: center; margin-top: 24px;">
        <form method="POST" action="{{ url_for('delete_task', task_id=task.id) }}" onsubmit="return confirm('Are you sure you want to delete this task?');">
            <button type="submit" class="btn btn-danger btn-sm">Delete Task</button>
        </form>
    </div>
    {% endif %}
</main>

<script>
async function delayTask(taskId, hours, days) {
    try {
        const response = await fetch(`/api/tasks/${taskId}/delay`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hours, days })
        });
        if (response.ok) {
            location.reload();
        }
    } catch (err) {
        console.error('Failed to delay task:', err);
    }
}

// Status button toggle
async function setStatus(status) {
    document.getElementById('status-input').value = status;

    const styles = {
        pending:   { border: '#F59E0B', bg: '#FEF3C7', color: '#92400E' },
        ongoing:   { border: '#6366F1', bg: '#E0E7FF', color: '#3730A3' },
        completed: { border: '#10B981', bg: '#D1FAE5', color: '#065F46' }
    };

    document.querySelectorAll('.status-btn').forEach(btn => {
        const s = btn.dataset.status;
        const active = s === status;
        btn.style.borderColor = active ? styles[s].border : '#E5E7EB';
        btn.style.background = active ? styles[s].bg : 'white';
        btn.style.color = active ? styles[s].color : '#6B7280';
    });

    // If completed, save immediately and go to dashboard
    if (status === 'completed') {
        const taskId = '{{ task.id }}';
        try {
            const response = await fetch(`/api/tasks/${taskId}/status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: 'completed' })
            });
            if (response.ok) {
                window.location.href = '{{ url_for("dashboard") }}';
            }
        } catch (err) {
            console.error('Failed to complete task:', err);
        }
    }
}

// Checklist toggle via API
async function toggleChecklistItem(taskId, itemId, isChecked) {
    try {
        const response = await fetch(`/api/tasks/${taskId}/checklist/${itemId}/toggle`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_completed: isChecked })
        });
        if (response.ok) {
            const label = event.target.closest('label');
            const span = label.querySelector('span');
            if (isChecked) {
                label.style.opacity = '0.6';
                span.style.textDecoration = 'line-through';
                span.style.color = 'var(--gray-500)';
            } else {
                label.style.opacity = '1';
                span.style.textDecoration = 'none';
                span.style.color = '';
            }
            // Update counter
            const checkboxes = document.querySelectorAll('.card-body input[type="checkbox"]');
            const checked = [...checkboxes].filter(c => c.checked).length;
            document.getElementById('checklist-counter').textContent = checked + '/' + checkboxes.length;
        }
    } catch (err) {
        console.error('Failed to toggle checklist item:', err);
    }
}
</script>
{% endblock %}
"""

# ============================================
# TASK DETAIL PAGE (with notes & checklist)
# ============================================

TASK_DETAIL_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<nav class="nav">
    <a href="{{ url_for('dashboard') }}" class="nav-brand">
        <svg viewBox="0 0 512 512" width="32" height="32">
            <defs>
                <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" style="stop-color:#8B5CF6" />
                    <stop offset="100%" style="stop-color:#6366F1" />
                </linearGradient>
            </defs>
            <rect width="512" height="512" rx="96" fill="white"/>
            <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
            <circle cx="310" cy="350" r="70" fill="#10B981"/>
            <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Jottask
    </a>
    <div class="nav-user">
        <a href="{{ url_for('dashboard') }}" class="btn btn-secondary btn-sm">← Back to Tasks</a>
    </div>
</nav>

<main class="main">
    <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 24px;">
        <!-- Main Content -->
        <div>
            <!-- Task Header -->
            <div class="card" style="margin-bottom: 24px;">
                <div class="card-body">
                    <div style="display: flex; align-items: start; justify-content: space-between; margin-bottom: 16px;">
                        <div>
                            <h1 style="font-size: 24px; margin-bottom: 8px;">{{ task.title }}</h1>
                            <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                                <span class="status-badge priority-{{ task.priority }}" style="background: var(--gray-100);">
                                    {{ task.priority|capitalize }} Priority
                                </span>
                                <span style="color: var(--gray-500);">
                                    Due: {{ task.due_date }} at {{ task.due_time[:5] if task.due_time else 'N/A' }}
                                </span>
                            </div>
                        </div>
                        <a href="{{ url_for('edit_task', task_id=task.id) }}" class="btn btn-secondary btn-sm">Edit</a>
                    </div>

                    {% if task.description %}
                    <p style="color: var(--gray-700); margin-top: 16px;">{{ task.description }}</p>
                    {% endif %}

                    {% if task.client_name %}
                    <div style="margin-top: 20px; padding: 16px; background: var(--gray-50); border-radius: 8px;">
                        <h4 style="font-size: 12px; color: var(--gray-500); margin-bottom: 8px;">CLIENT</h4>
                        <p style="font-weight: 500;">{{ task.client_name }}</p>
                        {% if task.client_email %}<p style="color: var(--gray-500); font-size: 14px;">{{ task.client_email }}</p>{% endif %}
                        {% if task.client_phone %}<p style="color: var(--gray-500); font-size: 14px;">{{ task.client_phone }}</p>{% endif %}
                    </div>
                    {% endif %}
                </div>
            </div>

            <!-- Checklist -->
            <div class="card" style="margin-bottom: 24px;">
                <div class="card-header">
                    <h3 class="card-title">Checklist</h3>
                    <span style="color: var(--gray-500); font-size: 14px;">
                        {{ checklist|selectattr('is_completed')|list|length }}/{{ checklist|length }} completed
                    </span>
                </div>
                <div class="card-body">
                    {% if checklist %}
                    <form method="POST" action="{{ url_for('update_checklist', task_id=task.id) }}">
                        {% for item in checklist %}
                        <div class="checklist-item {% if item.is_completed %}completed{% endif %}">
                            <input type="checkbox" name="completed" value="{{ item.id }}"
                                   id="item_{{ item.id }}" {% if item.is_completed %}checked{% endif %}
                                   onchange="this.form.submit()">
                            <label for="item_{{ item.id }}">{{ item.item_text }}</label>
                        </div>
                        {% endfor %}
                    </form>
                    {% else %}
                    <p style="color: var(--gray-500); text-align: center; padding: 20px;">No checklist items yet</p>
                    {% endif %}

                    <form method="POST" action="{{ url_for('add_checklist_item', task_id=task.id) }}" style="margin-top: 16px; display: flex; gap: 8px;">
                        <input type="text" name="item_text" class="form-input" placeholder="Add checklist item..." style="flex: 1;">
                        <button type="submit" class="btn btn-primary btn-sm">Add</button>
                    </form>
                </div>
            </div>

            <!-- Notes -->
            <div class="card">
                <div class="card-header">
                    <h3 class="card-title">Notes</h3>
                </div>
                <div class="card-body">
                    <form method="POST" action="{{ url_for('add_note', task_id=task.id) }}" style="margin-bottom: 20px;">
                        <textarea name="content" class="form-input" rows="3" placeholder="Add a note..."></textarea>
                        <button type="submit" class="btn btn-primary btn-sm" style="margin-top: 8px;">Add Note</button>
                    </form>

                    {% if notes %}
                    <div class="notes-list">
                        {% for note in notes %}
                        <div class="note-item" style="padding: 16px 0; border-bottom: 1px solid var(--gray-100);">
                            <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                                <span style="font-size: 12px; color: var(--gray-500);">
                                    {% if note.source == 'email' %}📧{% elif note.source == 'system' %}🤖{% else %}📝{% endif %}
                                    {{ note.source|capitalize }}
                                </span>
                                <span style="font-size: 12px; color: var(--gray-500);">
                                    {{ note.created_at[:16].replace('T', ' ') }}
                                </span>
                            </div>
                            <p style="color: var(--gray-700);">{{ note.content }}</p>
                            {% if note.source_email_subject %}
                            <p style="font-size: 12px; color: var(--gray-500); margin-top: 8px;">
                                Re: {{ note.source_email_subject }}
                            </p>
                            {% endif %}
                        </div>
                        {% endfor %}
                    </div>
                    {% else %}
                    <p style="color: var(--gray-500); text-align: center; padding: 20px;">No notes yet</p>
                    {% endif %}
                </div>
            </div>
        </div>

        <!-- Sidebar -->
        <div>
            <!-- Quick Actions -->
            <div class="card" style="margin-bottom: 24px;">
                <div class="card-header">
                    <h3 class="card-title">Quick Actions</h3>
                </div>
                <div class="card-body">
                    {% if task.status == 'pending' %}
                    <form method="POST" action="{{ url_for('complete_task_action', task_id=task.id) }}">
                        <button type="submit" class="btn btn-success" style="width: 100%; margin-bottom: 12px;">
                            ✓ Mark Complete
                        </button>
                    </form>
                    {% else %}
                    <form method="POST" action="{{ url_for('reopen_task', task_id=task.id) }}">
                        <button type="submit" class="btn btn-secondary" style="width: 100%; margin-bottom: 12px;">
                            ↩ Reopen Task
                        </button>
                    </form>
                    {% endif %}

                    <p style="font-size: 12px; color: var(--gray-500); margin-bottom: 8px;">Reschedule:</p>
                    <div class="delay-buttons">
                        <button class="delay-btn" onclick="delayTask('{{ task.id }}', 1, 0)">+1hr</button>
                        <button class="delay-btn" onclick="delayTask('{{ task.id }}', 0, 1)">+1d</button>
                        <button class="delay-btn" onclick="delayTask('{{ task.id }}', 0, 7)">+1w</button>
                    </div>
                </div>
            </div>

            <!-- Task Info -->
            <div class="card">
                <div class="card-header">
                    <h3 class="card-title">Details</h3>
                </div>
                <div class="card-body" style="font-size: 14px;">
                    <div style="display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--gray-100);">
                        <span style="color: var(--gray-500);">Status</span>
                        <span style="font-weight: 500;">{{ task.status|capitalize }}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--gray-100);">
                        <span style="color: var(--gray-500);">Priority</span>
                        <span class="priority-{{ task.priority }}">{{ task.priority|capitalize }}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--gray-100);">
                        <span style="color: var(--gray-500);">Created</span>
                        <span>{{ task.created_at[:10] if task.created_at else 'N/A' }}</span>
                    </div>
                    {% if task.completed_at %}
                    <div style="display: flex; justify-content: space-between; padding: 8px 0;">
                        <span style="color: var(--gray-500);">Completed</span>
                        <span>{{ task.completed_at[:10] }}</span>
                    </div>
                    {% endif %}
                </div>
            </div>
        </div>
    </div>
</main>

<style>
.checklist-item {
    display: flex;
    align-items: center;
    padding: 12px 0;
    border-bottom: 1px solid var(--gray-100);
    gap: 12px;
}
.checklist-item:last-of-type { border-bottom: none; }
.checklist-item input[type="checkbox"] {
    width: 20px;
    height: 20px;
    accent-color: var(--primary);
}
.checklist-item.completed label {
    text-decoration: line-through;
    color: var(--gray-500);
}
</style>

<script>
async function delayTask(taskId, hours, days) {
    const response = await fetch(`/api/tasks/${taskId}/delay`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hours, days })
    });
    if (response.ok) location.reload();
}
</script>
{% endblock %}
"""

# ============================================
# LANDING PAGE
# ============================================

LANDING_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jottask - AI-Powered Task Management</title>
    <meta name="description" content="Turn your emails into actionable tasks automatically. AI-powered task management for busy professionals.">
    <style>
        :root {
            --primary: #6366F1;
            --primary-dark: #4F46E5;
            --success: #10B981;
            --gray-50: #F9FAFB;
            --gray-100: #F3F4F6;
            --gray-200: #E5E7EB;
            --gray-500: #6B7280;
            --gray-700: #374151;
            --gray-900: #111827;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: var(--gray-900);
            line-height: 1.6;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 24px;
        }

        /* Header */
        header {
            background: white;
            border-bottom: 1px solid var(--gray-200);
            padding: 16px 0;
            position: sticky;
            top: 0;
            z-index: 100;
        }

        header .container {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
            text-decoration: none;
            color: var(--primary);
            font-weight: 700;
            font-size: 22px;
        }

        .logo svg { width: 36px; height: 36px; }

        .header-links {
            display: flex;
            align-items: center;
            gap: 32px;
        }

        .header-links a {
            text-decoration: none;
            color: var(--gray-700);
            font-weight: 500;
        }

        .header-links a:hover { color: var(--primary); }

        .btn {
            padding: 12px 24px;
            border-radius: 8px;
            font-weight: 600;
            text-decoration: none;
            display: inline-block;
            transition: all 0.2s;
        }

        .btn-primary {
            background: var(--primary);
            color: white;
        }

        .btn-primary:hover { background: var(--primary-dark); }

        .btn-secondary {
            background: var(--gray-100);
            color: var(--gray-700);
        }

        /* Hero */
        .hero {
            padding: 80px 0 100px;
            background: linear-gradient(180deg, white 0%, var(--gray-50) 100%);
            text-align: center;
        }

        .hero h1 {
            font-size: 56px;
            font-weight: 800;
            line-height: 1.1;
            margin-bottom: 24px;
            background: linear-gradient(135deg, var(--gray-900) 0%, var(--primary) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .hero p {
            font-size: 20px;
            color: var(--gray-500);
            max-width: 600px;
            margin: 0 auto 40px;
        }

        .hero-buttons {
            display: flex;
            gap: 16px;
            justify-content: center;
        }

        .hero-buttons .btn { padding: 16px 32px; font-size: 18px; }

        /* Features */
        .features {
            padding: 100px 0;
            background: white;
        }

        .features h2 {
            text-align: center;
            font-size: 40px;
            margin-bottom: 60px;
        }

        .features-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 40px;
        }

        .feature-card {
            text-align: center;
            padding: 32px;
        }

        .feature-icon {
            width: 64px;
            height: 64px;
            background: linear-gradient(135deg, var(--primary) 0%, #8B5CF6 100%);
            border-radius: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 20px;
            font-size: 28px;
        }

        .feature-card h3 {
            font-size: 20px;
            margin-bottom: 12px;
        }

        .feature-card p {
            color: var(--gray-500);
        }

        /* How it works */
        .how-it-works {
            padding: 100px 0;
            background: var(--gray-50);
        }

        .how-it-works h2 {
            text-align: center;
            font-size: 40px;
            margin-bottom: 60px;
        }

        .steps {
            display: flex;
            justify-content: center;
            gap: 60px;
        }

        .step {
            text-align: center;
            max-width: 280px;
        }

        .step-number {
            width: 48px;
            height: 48px;
            background: var(--primary);
            color: white;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 20px;
            margin: 0 auto 20px;
        }

        .step h3 {
            margin-bottom: 8px;
        }

        .step p {
            color: var(--gray-500);
        }

        /* Pricing */
        .pricing {
            padding: 100px 0;
            background: white;
        }

        .pricing h2 {
            text-align: center;
            font-size: 40px;
            margin-bottom: 16px;
        }

        .pricing > p {
            text-align: center;
            color: var(--gray-500);
            margin-bottom: 60px;
        }

        .pricing-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 32px;
            max-width: 1000px;
            margin: 0 auto;
        }

        .pricing-card {
            border: 2px solid var(--gray-200);
            border-radius: 16px;
            padding: 32px;
            text-align: center;
        }

        .pricing-card.popular {
            border-color: var(--primary);
            position: relative;
        }

        .pricing-card.popular::before {
            content: 'Most Popular';
            position: absolute;
            top: -12px;
            left: 50%;
            transform: translateX(-50%);
            background: var(--primary);
            color: white;
            padding: 4px 16px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }

        .pricing-card h3 {
            font-size: 24px;
            margin-bottom: 8px;
        }

        .pricing-card .price {
            font-size: 48px;
            font-weight: 700;
            color: var(--gray-900);
        }

        .pricing-card .price span {
            font-size: 16px;
            color: var(--gray-500);
            font-weight: 400;
        }

        .pricing-card ul {
            list-style: none;
            margin: 24px 0;
            text-align: left;
        }

        .pricing-card li {
            padding: 8px 0;
            color: var(--gray-700);
        }

        .pricing-card li::before {
            content: '✓';
            color: var(--success);
            margin-right: 8px;
        }

        .pricing-card .btn {
            width: 100%;
            margin-top: 16px;
        }

        /* CTA */
        .cta {
            padding: 100px 0;
            background: linear-gradient(135deg, var(--primary) 0%, #8B5CF6 100%);
            text-align: center;
            color: white;
        }

        .cta h2 {
            font-size: 40px;
            margin-bottom: 16px;
        }

        .cta p {
            font-size: 20px;
            opacity: 0.9;
            margin-bottom: 32px;
        }

        .cta .btn {
            background: white;
            color: var(--primary);
            padding: 16px 40px;
            font-size: 18px;
        }

        .cta .btn:hover {
            background: var(--gray-100);
        }

        /* Footer */
        footer {
            padding: 60px 0;
            background: var(--gray-900);
            color: var(--gray-500);
        }

        footer .container {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        footer a {
            color: var(--gray-500);
            text-decoration: none;
        }

        footer a:hover { color: white; }

        /* Mobile login link - visible only on mobile */
        .mobile-login {
            display: none;
            color: var(--primary);
            font-weight: 600;
            text-decoration: none;
            padding: 8px 16px;
            border: 2px solid var(--primary);
            border-radius: 8px;
        }

        /* Responsive */
        @media (max-width: 768px) {
            .hero h1 { font-size: 36px; }
            .features-grid { grid-template-columns: 1fr; }
            .steps { flex-direction: column; align-items: center; }
            .pricing-grid { grid-template-columns: 1fr; }
            .header-links { display: none; }
            .mobile-login { display: block; }
        }
    </style>
</head>
<body>
    <header>
        <div class="container">
            <a href="/" class="logo">
                <svg viewBox="0 0 512 512">
                    <defs>
                        <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                            <stop offset="0%" style="stop-color:#8B5CF6" />
                            <stop offset="100%" style="stop-color:#6366F1" />
                        </linearGradient>
                    </defs>
                    <rect width="512" height="512" rx="96" fill="white"/>
                    <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
                    <circle cx="310" cy="350" r="70" fill="#10B981"/>
                    <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                Jottask
            </a>
            <a href="/login" class="mobile-login">Login</a>
            <div class="header-links">
                <a href="#features">Features</a>
                <a href="#pricing">Pricing</a>
                <a href="/login">Login</a>
                <a href="/signup" class="btn btn-primary">Start Free Trial</a>
            </div>
        </div>
    </header>

    <section class="hero">
        <div class="container">
            <h1>Turn Emails Into<br>Actionable Tasks</h1>
            <p>Just CC <strong>jottask@flowquote.ai</strong> on any email. Our AI instantly creates tasks with due dates, priorities, and client info.</p>
            <div class="hero-buttons">
                <a href="/signup" class="btn btn-primary">Start Free 14-Day Trial</a>
                <a href="#features" class="btn btn-secondary">See How It Works</a>
            </div>
        </div>
    </section>

    <section class="features" id="features">
        <div class="container">
            <h2>Everything You Need to Stay Organized</h2>
            <div class="features-grid">
                <div class="feature-card">
                    <div class="feature-icon">📧</div>
                    <h3>CC to Create Tasks</h3>
                    <p>Just CC our email address on any conversation. We'll automatically extract tasks, due dates, and client info.</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon">🤖</div>
                    <h3>AI Summaries</h3>
                    <p>Get intelligent summaries of task context and conversation history before each reminder.</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon">⏰</div>
                    <h3>Smart Reminders</h3>
                    <p>Receive timely reminders with one-click actions to complete, delay, or reschedule.</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon">📋</div>
                    <h3>Checklists</h3>
                    <p>Break down tasks into actionable steps. AI can even suggest checklist items.</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon">🔄</div>
                    <h3>Project Stages</h3>
                    <p>Track tasks through your workflow with customizable project statuses.</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon">📱</div>
                    <h3>Works Everywhere</h3>
                    <p>Access your tasks from any device. Mobile-friendly interface for on-the-go management.</p>
                </div>
            </div>
        </div>
    </section>

    <section class="how-it-works">
        <div class="container">
            <h2>How It Works</h2>
            <div class="steps">
                <div class="step">
                    <div class="step-number">1</div>
                    <h3>Sign Up</h3>
                    <p>Create your free account with your email address.</p>
                </div>
                <div class="step">
                    <div class="step-number">2</div>
                    <h3>CC or Forward Emails</h3>
                    <p>CC <strong>jottask@flowquote.ai</strong> on any email you want turned into a task.</p>
                </div>
                <div class="step">
                    <div class="step-number">3</div>
                    <h3>AI Creates Tasks</h3>
                    <p>Our AI extracts tasks, due dates, and client info automatically.</p>
                </div>
            </div>
            <div style="text-align: center; margin-top: 40px; padding: 24px; background: white; border-radius: 12px; border: 2px dashed var(--primary);">
                <p style="color: var(--gray-500); margin-bottom: 8px;">Your Jottask email address:</p>
                <p style="font-size: 24px; font-weight: 700; color: var(--primary);">jottask@flowquote.ai</p>
                <p style="color: var(--gray-500); margin-top: 8px; font-size: 14px;">CC this address on any email to create a task</p>
            </div>
        </div>
    </section>

    <section class="pricing" id="pricing">
        <div class="container">
            <h2>Simple, Transparent Pricing</h2>
            <p>Start free, upgrade when you need more.</p>
            <div class="pricing-grid">
                <div class="pricing-card">
                    <h3>Starter</h3>
                    <div class="price">$0<span>/month</span></div>
                    <ul>
                        <li>Up to 50 tasks</li>
                        <li>1 email connection</li>
                        <li>Basic task management</li>
                        <li>Email reminders</li>
                    </ul>
                    <a href="/signup" class="btn btn-secondary">Get Started</a>
                </div>
                <div class="pricing-card popular">
                    <h3>Pro</h3>
                    <div class="price">$19<span>/month</span></div>
                    <ul>
                        <li>Up to 500 tasks</li>
                        <li>3 email connections</li>
                        <li>AI task summaries</li>
                        <li>Custom project statuses</li>
                        <li>Priority support</li>
                    </ul>
                    <a href="/signup" class="btn btn-primary">Start Free Trial</a>
                </div>
                <div class="pricing-card">
                    <h3>Business</h3>
                    <div class="price">$49<span>/month</span></div>
                    <ul>
                        <li>Unlimited tasks</li>
                        <li>10 email connections</li>
                        <li>Team collaboration</li>
                        <li>API access</li>
                        <li>Dedicated support</li>
                    </ul>
                    <a href="/signup" class="btn btn-secondary">Contact Sales</a>
                </div>
            </div>
        </div>
    </section>

    <section class="cta">
        <div class="container">
            <h2>Ready to Get Organized?</h2>
            <p>Join thousands of professionals who use Jottask to stay on top of their work.</p>
            <a href="/signup" class="btn">Start Your Free Trial</a>
        </div>
    </section>

    <footer>
        <div class="container">
            <span>© 2026 Jottask. All rights reserved.</span>
            <div style="display: flex; gap: 24px;">
                <a href="/privacy">Privacy</a>
                <a href="/terms">Terms</a>
                <a href="mailto:support@jottask.com">Contact</a>
            </div>
        </div>
    </footer>
</body>
</html>
"""

# ============================================
# PRICING PAGE (Standalone)
# ============================================

PRICING_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<nav class="nav">
    <a href="/" class="nav-brand">
        <svg viewBox="0 0 512 512" width="32" height="32">
            <defs>
                <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" style="stop-color:#8B5CF6" />
                    <stop offset="100%" style="stop-color:#6366F1" />
                </linearGradient>
            </defs>
            <rect width="512" height="512" rx="96" fill="white"/>
            <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
            <circle cx="310" cy="350" r="70" fill="#10B981"/>
            <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Jottask
    </a>
    <div class="nav-links">
        <a href="{{ url_for('dashboard') }}" class="nav-link">Dashboard</a>
        <a href="{{ url_for('projects') }}" class="nav-link">Projects</a>
        <a href="{{ url_for('settings') }}" class="nav-link">Settings</a>
    </div>
</nav>

<main class="main" style="max-width: 1000px; padding-top: 40px;">
    <div style="text-align: center; margin-bottom: 48px;">
        <h1 style="font-size: 36px; margin-bottom: 12px;">Choose Your Plan</h1>
        <p style="color: var(--gray-500);">
            Currently on: <strong>{{ current_plan|capitalize }}</strong>
            {% if subscription_status == 'trial' %} (Trial){% endif %}
        </p>
    </div>

    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px;">
        {% for plan in plans %}
        <div class="card" style="{% if plan.popular %}border: 2px solid var(--primary);{% endif %} position: relative;">
            {% if plan.popular %}
            <div style="position: absolute; top: -12px; left: 50%; transform: translateX(-50%); background: var(--primary); color: white; padding: 4px 16px; border-radius: 12px; font-size: 12px; font-weight: 600;">
                Most Popular
            </div>
            {% endif %}
            <div class="card-body" style="text-align: center; padding: 32px;">
                <h3 style="font-size: 24px; margin-bottom: 8px;">{{ plan.name }}</h3>
                <div style="font-size: 48px; font-weight: 700;">
                    ${{ plan.price_monthly }}
                    <span style="font-size: 16px; color: var(--gray-500); font-weight: 400;">/month</span>
                </div>

                <ul style="list-style: none; text-align: left; margin: 24px 0;">
                    {% for feature in plan.features %}
                    <li style="padding: 8px 0; color: var(--gray-700);">
                        <span style="color: var(--success); margin-right: 8px;">✓</span>
                        {{ feature }}
                    </li>
                    {% endfor %}
                </ul>

                {% if plan.id == current_plan %}
                <button class="btn btn-secondary" style="width: 100%;" disabled>Current Plan</button>
                {% elif plan.id == 'starter' %}
                <a href="{{ url_for('billing.customer_portal') }}" class="btn btn-secondary" style="width: 100%; display: block;">Downgrade</a>
                {% else %}
                <a href="{{ url_for('billing.create_checkout_session', plan=plan.id + '_monthly') }}" class="btn btn-primary" style="width: 100%; display: block;">
                    Upgrade to {{ plan.name }}
                </a>
                {% endif %}
            </div>
        </div>
        {% endfor %}
    </div>

    <div style="text-align: center; margin-top: 40px; color: var(--gray-500);">
        <p>All plans include a 14-day free trial. No credit card required to start.</p>
        <p style="margin-top: 8px;">
            <a href="{{ url_for('billing.customer_portal') }}" style="color: var(--primary);">Manage existing subscription →</a>
        </p>
    </div>
</main>
{% endblock %}
"""
