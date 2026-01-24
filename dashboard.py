"""
Jottask Dashboard - Main Web Application
Full SaaS task management interface
"""

import os
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, flash
from datetime import datetime, timedelta
import pytz
from functools import wraps
from supabase import create_client, Client

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))

# Register blueprints
from billing import billing_bp
from onboarding import onboarding_bp
from email_setup import email_setup_bp
app.register_blueprint(billing_bp)
app.register_blueprint(onboarding_bp)
app.register_blueprint(email_setup_bp)

# Supabase client
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================
# AUTH HELPERS
# ============================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_user_timezone():
    return pytz.timezone(session.get('timezone', 'Australia/Brisbane'))

# ============================================
# BASE TEMPLATE
# ============================================

BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }} - Jottask</title>
    <style>
        :root {
            --primary: #6366F1;
            --primary-dark: #4F46E5;
            --success: #10B981;
            --warning: #F59E0B;
            --danger: #EF4444;
            --gray-50: #F9FAFB;
            --gray-100: #F3F4F6;
            --gray-200: #E5E7EB;
            --gray-300: #D1D5DB;
            --gray-500: #6B7280;
            --gray-700: #374151;
            --gray-900: #111827;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--gray-50);
            color: var(--gray-900);
            line-height: 1.5;
        }

        /* Navigation */
        .nav {
            background: white;
            border-bottom: 1px solid var(--gray-200);
            padding: 0 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            height: 64px;
            position: sticky;
            top: 0;
            z-index: 100;
        }

        .nav-brand {
            display: flex;
            align-items: center;
            gap: 12px;
            text-decoration: none;
            color: var(--primary);
            font-weight: 700;
            font-size: 20px;
        }

        .nav-brand svg {
            width: 32px;
            height: 32px;
        }

        .nav-links {
            display: flex;
            gap: 8px;
        }

        .nav-link {
            padding: 8px 16px;
            text-decoration: none;
            color: var(--gray-700);
            border-radius: 8px;
            font-weight: 500;
            transition: all 0.2s;
        }

        .nav-link:hover, .nav-link.active {
            background: var(--gray-100);
            color: var(--primary);
        }

        .nav-user {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .avatar {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: var(--primary);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
        }

        /* Main Layout */
        .main {
            max-width: 1200px;
            margin: 0 auto;
            padding: 24px;
        }

        /* Cards */
        .card {
            background: white;
            border-radius: 12px;
            border: 1px solid var(--gray-200);
            overflow: hidden;
        }

        .card-header {
            padding: 16px 20px;
            border-bottom: 1px solid var(--gray-200);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .card-title {
            font-size: 16px;
            font-weight: 600;
        }

        .card-body {
            padding: 20px;
        }

        /* Buttons */
        .btn {
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            border: none;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s;
        }

        .btn-primary {
            background: var(--primary);
            color: white;
        }

        .btn-primary:hover {
            background: var(--primary-dark);
        }

        .btn-secondary {
            background: var(--gray-100);
            color: var(--gray-700);
        }

        .btn-secondary:hover {
            background: var(--gray-200);
        }

        .btn-success {
            background: var(--success);
            color: white;
        }

        .btn-danger {
            background: var(--danger);
            color: white;
        }

        .btn-sm {
            padding: 6px 12px;
            font-size: 13px;
        }

        /* Forms */
        .form-group {
            margin-bottom: 16px;
        }

        .form-label {
            display: block;
            margin-bottom: 6px;
            font-weight: 500;
            font-size: 14px;
            color: var(--gray-700);
        }

        .form-input {
            width: 100%;
            padding: 10px 14px;
            border: 1px solid var(--gray-300);
            border-radius: 8px;
            font-size: 14px;
            transition: all 0.2s;
        }

        .form-input:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
        }

        /* Task List */
        .task-list {
            display: flex;
            flex-direction: column;
        }

        .task-item {
            display: flex;
            align-items: center;
            padding: 16px 20px;
            border-bottom: 1px solid var(--gray-100);
            gap: 16px;
            transition: background 0.2s;
        }

        .task-item:hover {
            background: var(--gray-50);
        }

        .task-item:last-child {
            border-bottom: none;
        }

        .task-checkbox {
            width: 22px;
            height: 22px;
            border-radius: 50%;
            border: 2px solid var(--gray-300);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            transition: all 0.2s;
        }

        .task-checkbox:hover {
            border-color: var(--success);
            background: rgba(16, 185, 129, 0.1);
        }

        .task-checkbox.completed {
            background: var(--success);
            border-color: var(--success);
        }

        .task-content {
            flex: 1;
            min-width: 0;
        }

        .task-title {
            font-weight: 500;
            color: var(--gray-900);
            margin-bottom: 4px;
        }

        .task-meta {
            display: flex;
            gap: 16px;
            font-size: 13px;
            color: var(--gray-500);
        }

        .task-actions {
            display: flex;
            gap: 8px;
            opacity: 0;
            transition: opacity 0.2s;
        }

        .task-item:hover .task-actions {
            opacity: 1;
        }

        /* Status Badge */
        .status-badge {
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
        }

        /* Priority */
        .priority-high { color: var(--danger); }
        .priority-medium { color: var(--warning); }
        .priority-low { color: var(--success); }

        /* Stats */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .stat-card {
            background: white;
            border-radius: 12px;
            border: 1px solid var(--gray-200);
            padding: 20px;
        }

        .stat-value {
            font-size: 32px;
            font-weight: 700;
            color: var(--gray-900);
        }

        .stat-label {
            font-size: 14px;
            color: var(--gray-500);
            margin-top: 4px;
        }

        /* Modal */
        .modal-backdrop {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            opacity: 0;
            visibility: hidden;
            transition: all 0.3s;
        }

        .modal-backdrop.active {
            opacity: 1;
            visibility: visible;
        }

        .modal {
            background: white;
            border-radius: 16px;
            width: 100%;
            max-width: 500px;
            max-height: 90vh;
            overflow-y: auto;
            transform: scale(0.9);
            transition: transform 0.3s;
        }

        .modal-backdrop.active .modal {
            transform: scale(1);
        }

        .modal-header {
            padding: 20px;
            border-bottom: 1px solid var(--gray-200);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .modal-title {
            font-size: 18px;
            font-weight: 600;
        }

        .modal-close {
            background: none;
            border: none;
            font-size: 24px;
            cursor: pointer;
            color: var(--gray-500);
        }

        .modal-body {
            padding: 20px;
        }

        .modal-footer {
            padding: 16px 20px;
            border-top: 1px solid var(--gray-200);
            display: flex;
            gap: 12px;
            justify-content: flex-end;
        }

        /* Delay Buttons */
        .delay-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 12px;
        }

        .delay-btn {
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
            border: 1px solid var(--gray-200);
            background: white;
            cursor: pointer;
            transition: all 0.2s;
        }

        .delay-btn:hover {
            border-color: var(--primary);
            background: rgba(99, 102, 241, 0.05);
        }

        /* Empty State */
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: var(--gray-500);
        }

        .empty-state-icon {
            font-size: 48px;
            margin-bottom: 16px;
        }

        /* Tabs */
        .tabs {
            display: flex;
            gap: 4px;
            border-bottom: 1px solid var(--gray-200);
            margin-bottom: 20px;
        }

        .tab {
            padding: 12px 20px;
            font-weight: 500;
            color: var(--gray-500);
            border-bottom: 2px solid transparent;
            cursor: pointer;
            transition: all 0.2s;
        }

        .tab:hover {
            color: var(--gray-700);
        }

        .tab.active {
            color: var(--primary);
            border-bottom-color: var(--primary);
        }

        /* Alert */
        .alert {
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 16px;
            font-size: 14px;
        }

        .alert-success {
            background: #D1FAE5;
            color: #065F46;
        }

        .alert-error {
            background: #FEE2E2;
            color: #991B1B;
        }

        /* Auth Pages */
        .auth-container {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%);
            padding: 20px;
        }

        .auth-card {
            background: white;
            border-radius: 16px;
            padding: 40px;
            width: 100%;
            max-width: 420px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.2);
        }

        .auth-logo {
            text-align: center;
            margin-bottom: 32px;
        }

        .auth-logo svg {
            width: 48px;
            height: 48px;
        }

        .auth-logo h1 {
            color: var(--primary);
            font-size: 24px;
            margin-top: 12px;
        }

        /* Responsive */
        @media (max-width: 768px) {
            .nav {
                padding: 0 16px;
            }

            .nav-links {
                display: none;
            }

            .main {
                padding: 16px;
            }

            .task-actions {
                opacity: 1;
            }
        }
    </style>
</head>
<body>
    {% block content %}{% endblock %}

    <script>
        // Modal handling
        function openModal(modalId) {
            document.getElementById(modalId).classList.add('active');
        }

        function closeModal(modalId) {
            document.getElementById(modalId).classList.remove('active');
        }

        // Task completion
        async function toggleTask(taskId, checkbox) {
            const isCompleted = checkbox.classList.contains('completed');
            const newStatus = isCompleted ? 'pending' : 'completed';

            try {
                const response = await fetch(`/api/tasks/${taskId}/status`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: newStatus })
                });

                if (response.ok) {
                    checkbox.classList.toggle('completed');
                    if (newStatus === 'completed') {
                        checkbox.innerHTML = '✓';
                        checkbox.closest('.task-item').style.opacity = '0.6';
                    } else {
                        checkbox.innerHTML = '';
                        checkbox.closest('.task-item').style.opacity = '1';
                    }
                }
            } catch (err) {
                console.error('Failed to update task:', err);
            }
        }

        // Quick delay
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
    </script>
</body>
</html>
"""

# ============================================
# AUTH PAGES
# ============================================

LOGIN_TEMPLATE = """
{% extends "base" %}
{% block content %}
<div class="auth-container">
    <div class="auth-card">
        <div class="auth-logo">
            <svg viewBox="0 0 512 512">
                <defs>
                    <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                        <stop offset="0%" style="stop-color:#8B5CF6" />
                        <stop offset="100%" style="stop-color:#6366F1" />
                    </linearGradient>
                </defs>
                <rect width="512" height="512" rx="96" fill="white"/>
                <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
                <line x1="160" y1="150" x2="300" y2="150" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <line x1="160" y1="200" x2="300" y2="200" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <line x1="160" y1="250" x2="260" y2="250" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <circle cx="310" cy="350" r="70" fill="#10B981"/>
                <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <h1>Jottask</h1>
        </div>

        {% if error %}
        <div class="alert alert-error">{{ error }}</div>
        {% endif %}

        <form method="POST">
            <div class="form-group">
                <label class="form-label">Email</label>
                <input type="email" name="email" class="form-input" required autofocus>
            </div>

            <div class="form-group">
                <label class="form-label">Password</label>
                <input type="password" name="password" class="form-input" required>
            </div>

            <button type="submit" class="btn btn-primary" style="width: 100%; margin-top: 8px;">
                Sign In
            </button>
        </form>

        <p style="text-align: center; margin-top: 24px; color: var(--gray-500);">
            Don't have an account? <a href="{{ url_for('signup') }}" style="color: var(--primary);">Sign up</a>
        </p>
    </div>
</div>
{% endblock %}
"""

SIGNUP_TEMPLATE = """
{% extends "base" %}
{% block content %}
<div class="auth-container">
    <div class="auth-card">
        <div class="auth-logo">
            <svg viewBox="0 0 512 512">
                <defs>
                    <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                        <stop offset="0%" style="stop-color:#8B5CF6" />
                        <stop offset="100%" style="stop-color:#6366F1" />
                    </linearGradient>
                </defs>
                <rect width="512" height="512" rx="96" fill="white"/>
                <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
                <line x1="160" y1="150" x2="300" y2="150" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <line x1="160" y1="200" x2="300" y2="200" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <line x1="160" y1="250" x2="260" y2="250" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <circle cx="310" cy="350" r="70" fill="#10B981"/>
                <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <h1>Jottask</h1>
        </div>

        <p style="text-align: center; color: var(--gray-500); margin-bottom: 24px;">
            Start your 14-day free trial
        </p>

        {% if error %}
        <div class="alert alert-error">{{ error }}</div>
        {% endif %}

        <form method="POST">
            <div class="form-group">
                <label class="form-label">Full Name</label>
                <input type="text" name="full_name" class="form-input" required>
            </div>

            <div class="form-group">
                <label class="form-label">Email</label>
                <input type="email" name="email" class="form-input" required>
            </div>

            <div class="form-group">
                <label class="form-label">Password</label>
                <input type="password" name="password" class="form-input" required minlength="8">
            </div>

            <div class="form-group">
                <label class="form-label">Timezone</label>
                <select name="timezone" class="form-input">
                    <option value="Australia/Brisbane">Australia/Brisbane (AEST)</option>
                    <option value="Australia/Sydney">Australia/Sydney (AEST/AEDT)</option>
                    <option value="Australia/Melbourne">Australia/Melbourne (AEST/AEDT)</option>
                    <option value="Australia/Perth">Australia/Perth (AWST)</option>
                    <option value="Pacific/Auckland">New Zealand (NZST)</option>
                    <option value="America/New_York">US Eastern</option>
                    <option value="America/Los_Angeles">US Pacific</option>
                    <option value="Europe/London">UK (GMT/BST)</option>
                </select>
            </div>

            <button type="submit" class="btn btn-primary" style="width: 100%; margin-top: 8px;">
                Create Account
            </button>
        </form>

        <p style="text-align: center; margin-top: 24px; color: var(--gray-500);">
            Already have an account? <a href="{{ url_for('login') }}" style="color: var(--primary);">Sign in</a>
        </p>
    </div>
</div>
{% endblock %}
"""

# ============================================
# DASHBOARD TEMPLATE
# ============================================

DASHBOARD_TEMPLATE = """
{% extends "base" %}
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

    <div class="nav-links">
        <a href="{{ url_for('dashboard') }}" class="nav-link active">Tasks</a>
        <a href="{{ url_for('projects') }}" class="nav-link">Projects</a>
        <a href="{{ url_for('settings') }}" class="nav-link">Settings</a>
    </div>

    <div class="nav-user">
        <span style="color: var(--gray-500);">{{ session.user_name }}</span>
        <div class="avatar">{{ session.user_name[0].upper() }}</div>
        <a href="{{ url_for('logout') }}" class="btn btn-secondary btn-sm">Logout</a>
    </div>
</nav>

<main class="main">
    <!-- Stats -->
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-value">{{ stats.pending }}</div>
            <div class="stat-label">Pending Tasks</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.due_today }}</div>
            <div class="stat-label">Due Today</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.overdue }}</div>
            <div class="stat-label">Overdue</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.completed_this_week }}</div>
            <div class="stat-label">Completed This Week</div>
        </div>
    </div>

    <!-- Task Card -->
    <div class="card">
        <div class="card-header">
            <h2 class="card-title">Tasks</h2>
            <button class="btn btn-primary" onclick="openModal('new-task-modal')">
                + New Task
            </button>
        </div>

        <div class="tabs" style="padding: 0 20px;">
            <div class="tab active" data-filter="all">All</div>
            <div class="tab" data-filter="today">Today</div>
            <div class="tab" data-filter="overdue">Overdue</div>
            <div class="tab" data-filter="completed">Completed</div>
        </div>

        <div class="task-list">
            {% if tasks %}
                {% for task in tasks %}
                <div class="task-item" data-task-id="{{ task.id }}">
                    <div class="task-checkbox {% if task.status == 'completed' %}completed{% endif %}"
                         onclick="toggleTask('{{ task.id }}', this)">
                        {% if task.status == 'completed' %}✓{% endif %}
                    </div>

                    <div class="task-content">
                        <div class="task-title">{{ task.title }}</div>
                        <div class="task-meta">
                            <span class="priority-{{ task.priority }}">{{ task.priority|capitalize }}</span>
                            <span>Due: {{ task.due_date }} {{ task.due_time[:5] if task.due_time else '' }}</span>
                            {% if task.client_name %}
                            <span>{{ task.client_name }}</span>
                            {% endif %}
                        </div>
                    </div>

                    <div class="task-actions">
                        <button class="btn btn-secondary btn-sm" onclick="openEditModal('{{ task.id }}')">Edit</button>
                        <button class="btn btn-secondary btn-sm" onclick="openDelayModal('{{ task.id }}')">Delay</button>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty-state">
                    <div class="empty-state-icon">📋</div>
                    <h3>No tasks yet</h3>
                    <p>Create your first task to get started</p>
                </div>
            {% endif %}
        </div>
    </div>
</main>

<!-- New Task Modal -->
<div id="new-task-modal" class="modal-backdrop" onclick="if(event.target === this) closeModal('new-task-modal')">
    <div class="modal">
        <div class="modal-header">
            <h3 class="modal-title">New Task</h3>
            <button class="modal-close" onclick="closeModal('new-task-modal')">&times;</button>
        </div>
        <form method="POST" action="{{ url_for('create_task') }}">
            <div class="modal-body">
                <div class="form-group">
                    <label class="form-label">Title</label>
                    <input type="text" name="title" class="form-input" required>
                </div>

                <div class="form-group">
                    <label class="form-label">Description</label>
                    <textarea name="description" class="form-input" rows="3"></textarea>
                </div>

                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                    <div class="form-group">
                        <label class="form-label">Due Date</label>
                        <input type="date" name="due_date" class="form-input" value="{{ today }}">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Due Time</label>
                        <input type="time" name="due_time" class="form-input" value="09:00">
                    </div>
                </div>

                <div class="form-group">
                    <label class="form-label">Priority</label>
                    <select name="priority" class="form-input">
                        <option value="low">Low</option>
                        <option value="medium" selected>Medium</option>
                        <option value="high">High</option>
                        <option value="urgent">Urgent</option>
                    </select>
                </div>

                <div class="form-group">
                    <label class="form-label">Client Name (optional)</label>
                    <input type="text" name="client_name" class="form-input">
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" onclick="closeModal('new-task-modal')">Cancel</button>
                <button type="submit" class="btn btn-primary">Create Task</button>
            </div>
        </form>
    </div>
</div>

<!-- Delay Modal -->
<div id="delay-modal" class="modal-backdrop" onclick="if(event.target === this) closeModal('delay-modal')">
    <div class="modal">
        <div class="modal-header">
            <h3 class="modal-title">Delay Task</h3>
            <button class="modal-close" onclick="closeModal('delay-modal')">&times;</button>
        </div>
        <div class="modal-body">
            <p style="color: var(--gray-500); margin-bottom: 16px;">Quick delay options:</p>
            <div class="delay-buttons">
                <button class="delay-btn" onclick="delayTask(currentTaskId, 1, 0)">+1 Hour</button>
                <button class="delay-btn" onclick="delayTask(currentTaskId, 3, 0)">+3 Hours</button>
                <button class="delay-btn" onclick="delayTask(currentTaskId, 0, 1)">+1 Day</button>
                <button class="delay-btn" onclick="delayTask(currentTaskId, 0, 7)">+1 Week</button>
            </div>

            <hr style="margin: 20px 0; border: none; border-top: 1px solid var(--gray-200);">

            <form method="POST" action="{{ url_for('delay_task_custom') }}" id="custom-delay-form">
                <input type="hidden" name="task_id" id="delay-task-id">
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                    <div class="form-group">
                        <label class="form-label">New Date</label>
                        <input type="date" name="new_date" class="form-input" value="{{ today }}">
                    </div>
                    <div class="form-group">
                        <label class="form-label">New Time</label>
                        <input type="time" name="new_time" class="form-input" value="09:00">
                    </div>
                </div>
                <button type="submit" class="btn btn-primary" style="width: 100%;">Set Custom Date/Time</button>
            </form>
        </div>
    </div>
</div>

<script>
    let currentTaskId = null;

    function openDelayModal(taskId) {
        currentTaskId = taskId;
        document.getElementById('delay-task-id').value = taskId;
        openModal('delay-modal');
    }

    function openEditModal(taskId) {
        window.location.href = `/tasks/${taskId}/edit`;
    }

    // Tab filtering
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', function() {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            this.classList.add('active');
            // Filter logic would go here
        });
    });
</script>
{% endblock %}
"""

# ============================================
# SETTINGS TEMPLATE
# ============================================

SETTINGS_TEMPLATE = """
{% extends "base" %}
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

    <div class="nav-links">
        <a href="{{ url_for('dashboard') }}" class="nav-link">Tasks</a>
        <a href="{{ url_for('projects') }}" class="nav-link">Projects</a>
        <a href="{{ url_for('settings') }}" class="nav-link active">Settings</a>
    </div>

    <div class="nav-user">
        <span style="color: var(--gray-500);">{{ session.user_name }}</span>
        <div class="avatar">{{ session.user_name[0].upper() }}</div>
        <a href="{{ url_for('logout') }}" class="btn btn-secondary btn-sm">Logout</a>
    </div>
</nav>

<main class="main">
    {% if message %}
    <div class="alert alert-success">{{ message }}</div>
    {% endif %}

    <div style="display: grid; grid-template-columns: 250px 1fr; gap: 24px;">
        <!-- Sidebar -->
        <div>
            <div class="card">
                <div class="card-body" style="padding: 8px;">
                    <a href="#profile" class="nav-link active" style="display: block;">Profile</a>
                    <a href="#notifications" class="nav-link" style="display: block;">Notifications</a>
                    <a href="#email" class="nav-link" style="display: block;">Email Connection</a>
                    <a href="#subscription" class="nav-link" style="display: block;">Subscription</a>
                </div>
            </div>
        </div>

        <!-- Content -->
        <div>
            <!-- Profile Section -->
            <div class="card" style="margin-bottom: 24px;">
                <div class="card-header">
                    <h2 class="card-title">Profile Settings</h2>
                </div>
                <div class="card-body">
                    <form method="POST" action="{{ url_for('update_profile') }}">
                        <div class="form-group">
                            <label class="form-label">Full Name</label>
                            <input type="text" name="full_name" class="form-input" value="{{ user.full_name or '' }}">
                        </div>

                        <div class="form-group">
                            <label class="form-label">Email</label>
                            <input type="email" class="form-input" value="{{ user.email }}" disabled>
                            <small style="color: var(--gray-500);">Contact support to change email</small>
                        </div>

                        <div class="form-group">
                            <label class="form-label">Company Name</label>
                            <input type="text" name="company_name" class="form-input" value="{{ user.company_name or '' }}">
                        </div>

                        <div class="form-group">
                            <label class="form-label">Timezone</label>
                            <select name="timezone" class="form-input">
                                <option value="Australia/Brisbane" {% if user.timezone == 'Australia/Brisbane' %}selected{% endif %}>Australia/Brisbane (AEST)</option>
                                <option value="Australia/Sydney" {% if user.timezone == 'Australia/Sydney' %}selected{% endif %}>Australia/Sydney</option>
                                <option value="Australia/Melbourne" {% if user.timezone == 'Australia/Melbourne' %}selected{% endif %}>Australia/Melbourne</option>
                                <option value="America/New_York" {% if user.timezone == 'America/New_York' %}selected{% endif %}>US Eastern</option>
                                <option value="Europe/London" {% if user.timezone == 'Europe/London' %}selected{% endif %}>UK (GMT/BST)</option>
                            </select>
                        </div>

                        <button type="submit" class="btn btn-primary">Save Changes</button>
                    </form>
                </div>
            </div>

            <!-- Daily Summary Section -->
            <div class="card" style="margin-bottom: 24px;">
                <div class="card-header">
                    <h2 class="card-title">Daily Summary</h2>
                </div>
                <div class="card-body">
                    <form method="POST" action="{{ url_for('update_summary_settings') }}">
                        <div class="form-group">
                            <label style="display: flex; align-items: center; gap: 12px; cursor: pointer;">
                                <input type="checkbox" name="daily_summary_enabled" {% if user.daily_summary_enabled %}checked{% endif %} style="width: 20px; height: 20px;">
                                <span class="form-label" style="margin: 0;">Enable daily summary email</span>
                            </label>
                            <small style="color: var(--gray-500); display: block; margin-top: 8px;">
                                Receive a daily email with your tasks and projects overview
                            </small>
                        </div>

                        <div class="form-group">
                            <label class="form-label">Summary Time</label>
                            <select name="daily_summary_time" class="form-input" style="max-width: 200px;">
                                <option value="06:00:00" {% if user.daily_summary_time == '06:00:00' %}selected{% endif %}>6:00 AM</option>
                                <option value="07:00:00" {% if user.daily_summary_time == '07:00:00' %}selected{% endif %}>7:00 AM</option>
                                <option value="08:00:00" {% if user.daily_summary_time == '08:00:00' or not user.daily_summary_time %}selected{% endif %}>8:00 AM</option>
                                <option value="09:00:00" {% if user.daily_summary_time == '09:00:00' %}selected{% endif %}>9:00 AM</option>
                            </select>
                            <small style="color: var(--gray-500);">Time in your local timezone ({{ user.timezone }})</small>
                        </div>

                        <button type="submit" class="btn btn-primary">Save Settings</button>
                    </form>
                </div>
            </div>

            <!-- Subscription Section -->
            <div class="card">
                <div class="card-header">
                    <h2 class="card-title">Subscription</h2>
                </div>
                <div class="card-body">
                    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
                        <span class="status-badge" style="background: {% if user.subscription_status == 'active' %}var(--success){% elif user.subscription_status == 'trial' %}var(--warning){% else %}var(--gray-300){% endif %}; color: white;">
                            {{ user.subscription_status|capitalize }}
                        </span>
                        <span style="font-weight: 600;">{{ user.subscription_tier|capitalize }} Plan</span>
                    </div>

                    {% if user.subscription_status == 'trial' %}
                    <p style="color: var(--gray-500); margin-bottom: 20px;">
                        Your trial ends on {{ user.trial_ends_at[:10] if user.trial_ends_at else 'soon' }}
                    </p>
                    {% endif %}

                    <a href="{{ url_for('billing') }}" class="btn btn-primary">Manage Subscription</a>
                </div>
            </div>
        </div>
    </div>
</main>
{% endblock %}
"""

# ============================================
# PROJECTS TEMPLATES
# ============================================

PROJECTS_TEMPLATE = """
{% extends "base" %}
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

    <div class="nav-links">
        <a href="{{ url_for('dashboard') }}" class="nav-link">Tasks</a>
        <a href="{{ url_for('projects') }}" class="nav-link active">Projects</a>
        <a href="{{ url_for('settings') }}" class="nav-link">Settings</a>
    </div>

    <div class="nav-user">
        <span style="color: var(--gray-500);">{{ session.user_name }}</span>
        <div class="avatar">{{ session.user_name[0].upper() }}</div>
        <a href="{{ url_for('logout') }}" class="btn btn-secondary btn-sm">Logout</a>
    </div>
</nav>

<main class="main">
    <!-- Stats -->
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-value">{{ stats.active }}</div>
            <div class="stat-label">Active Projects</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.total_items }}</div>
            <div class="stat-label">Total Items</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.completed_items }}</div>
            <div class="stat-label">Items Completed</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.completed_projects }}</div>
            <div class="stat-label">Projects Completed</div>
        </div>
    </div>

    <!-- Projects Card -->
    <div class="card">
        <div class="card-header">
            <h2 class="card-title">Projects</h2>
            <a href="{{ url_for('project_create') }}" class="btn btn-primary">
                + New Project
            </a>
        </div>

        <div class="tabs" style="padding: 0 20px;">
            <div class="tab {% if filter == 'active' %}active{% endif %}" onclick="location.href='?filter=active'">Active</div>
            <div class="tab {% if filter == 'completed' %}active{% endif %}" onclick="location.href='?filter=completed'">Completed</div>
            <div class="tab {% if filter == 'archived' %}active{% endif %}" onclick="location.href='?filter=archived'">Archived</div>
            <div class="tab {% if filter == 'all' %}active{% endif %}" onclick="location.href='?filter=all'">All</div>
        </div>

        <div class="project-list">
            {% if projects %}
                {% for project in projects %}
                <a href="{{ url_for('project_detail', project_id=project.id) }}" class="project-item" style="text-decoration: none; color: inherit;">
                    <div class="project-color" style="background: {{ project.color or '#6366F1' }};"></div>

                    <div class="project-content">
                        <div class="project-title">{{ project.name }}</div>
                        <div class="project-meta">
                            <span>{{ project.item_count or 0 }} items</span>
                            {% if project.description %}
                            <span>{{ project.description[:50] }}{% if project.description|length > 50 %}...{% endif %}</span>
                            {% endif %}
                        </div>
                    </div>

                    <div class="project-progress">
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: {{ project.progress or 0 }}%;"></div>
                        </div>
                        <span class="progress-text">{{ project.progress or 0 }}%</span>
                    </div>

                    <div class="project-status">
                        <span class="status-badge" style="background: {% if project.status == 'active' %}var(--primary){% elif project.status == 'completed' %}var(--success){% else %}var(--gray-300){% endif %}; color: white;">
                            {{ project.status|capitalize }}
                        </span>
                    </div>
                </a>
                {% endfor %}
            {% else %}
                <div class="empty-state">
                    <div class="empty-state-icon">📁</div>
                    <h3>No projects yet</h3>
                    <p>Create your first project or email "Project: Name - items" to jottask@flowquote.ai</p>
                </div>
            {% endif %}
        </div>
    </div>
</main>

<style>
.project-list {
    display: flex;
    flex-direction: column;
}

.project-item {
    display: flex;
    align-items: center;
    padding: 16px 20px;
    border-bottom: 1px solid var(--gray-100);
    gap: 16px;
    transition: background 0.2s;
}

.project-item:hover {
    background: var(--gray-50);
}

.project-item:last-child {
    border-bottom: none;
}

.project-color {
    width: 8px;
    height: 48px;
    border-radius: 4px;
    flex-shrink: 0;
}

.project-content {
    flex: 1;
    min-width: 0;
}

.project-title {
    font-weight: 600;
    color: var(--gray-900);
    margin-bottom: 4px;
}

.project-meta {
    display: flex;
    gap: 16px;
    font-size: 13px;
    color: var(--gray-500);
}

.project-progress {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 150px;
}

.progress-bar {
    flex: 1;
    height: 8px;
    background: var(--gray-200);
    border-radius: 4px;
    overflow: hidden;
}

.progress-fill {
    height: 100%;
    background: var(--success);
    transition: width 0.3s;
}

.progress-text {
    font-size: 13px;
    color: var(--gray-500);
    min-width: 35px;
}

.project-status {
    flex-shrink: 0;
}

@media (max-width: 768px) {
    .project-progress {
        display: none;
    }
}
</style>
{% endblock %}
"""

PROJECT_DETAIL_TEMPLATE = """
{% extends "base" %}
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
        <a href="{{ url_for('projects') }}" class="btn btn-secondary btn-sm">← Back to Projects</a>
    </div>
</nav>

<main class="main" style="max-width: 800px;">
    <!-- Project Header -->
    <div class="card" style="margin-bottom: 24px;">
        <div class="card-body">
            <div style="display: flex; align-items: start; justify-content: space-between; margin-bottom: 16px;">
                <div style="display: flex; align-items: center; gap: 16px;">
                    <div style="width: 12px; height: 48px; border-radius: 6px; background: {{ project.color or '#6366F1' }};"></div>
                    <div>
                        <h1 style="font-size: 24px; margin-bottom: 4px;">{{ project.name }}</h1>
                        <span class="status-badge" style="background: {% if project.status == 'active' %}var(--primary){% elif project.status == 'completed' %}var(--success){% else %}var(--gray-300){% endif %}; color: white;">
                            {{ project.status|capitalize }}
                        </span>
                    </div>
                </div>
                <div style="display: flex; gap: 8px;">
                    {% if project.status == 'active' %}
                    <form method="POST" action="{{ url_for('project_complete', project_id=project.id) }}" style="display: inline;">
                        <button type="submit" class="btn btn-success btn-sm">Mark Complete</button>
                    </form>
                    {% elif project.status == 'completed' %}
                    <form method="POST" action="{{ url_for('project_reopen', project_id=project.id) }}" style="display: inline;">
                        <button type="submit" class="btn btn-secondary btn-sm">Reopen</button>
                    </form>
                    {% endif %}
                    <form method="POST" action="{{ url_for('project_delete', project_id=project.id) }}" onsubmit="return confirm('Delete this project and all its items?');" style="display: inline;">
                        <button type="submit" class="btn btn-danger btn-sm">Delete</button>
                    </form>
                </div>
            </div>

            {% if project.description %}
            <p style="color: var(--gray-600); margin-bottom: 16px;">{{ project.description }}</p>
            {% endif %}

            <!-- Progress Bar -->
            <div style="display: flex; align-items: center; gap: 12px;">
                <div style="flex: 1; height: 12px; background: var(--gray-200); border-radius: 6px; overflow: hidden;">
                    <div style="height: 100%; background: var(--success); width: {{ progress }}%; transition: width 0.3s;"></div>
                </div>
                <span style="font-weight: 600; color: var(--gray-700);">{{ completed_count }}/{{ total_count }} ({{ progress }}%)</span>
            </div>
        </div>
    </div>

    <!-- Checklist Items -->
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Checklist</h3>
        </div>
        <div class="card-body">
            {% if items %}
            <div class="checklist">
                {% for item in items %}
                <div class="checklist-item {% if item.is_completed %}completed{% endif %}">
                    <form method="POST" action="{{ url_for('project_item_toggle', project_id=project.id, item_id=item.id) }}" style="display: contents;">
                        <button type="submit" class="item-checkbox {% if item.is_completed %}checked{% endif %}">
                            {% if item.is_completed %}✓{% endif %}
                        </button>
                    </form>
                    <span class="item-text">{{ item.item_text }}</span>
                    <span class="item-source" style="font-size: 11px; color: var(--gray-400); margin-left: auto;">{{ item.source }}</span>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <p style="color: var(--gray-500); text-align: center; padding: 20px;">No items yet</p>
            {% endif %}

            <!-- Add Item Form -->
            <form method="POST" action="{{ url_for('project_item_add', project_id=project.id) }}" style="margin-top: 20px; display: flex; gap: 8px;">
                <input type="text" name="item_text" class="form-input" placeholder="Add a checklist item..." required style="flex: 1;">
                <button type="submit" class="btn btn-primary">Add</button>
            </form>
        </div>
    </div>

    <!-- Project Info -->
    <div class="card" style="margin-top: 24px;">
        <div class="card-header">
            <h3 class="card-title">Project Details</h3>
        </div>
        <div class="card-body" style="font-size: 14px;">
            <div style="display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--gray-100);">
                <span style="color: var(--gray-500);">Created</span>
                <span>{{ project.created_at[:10] if project.created_at else 'N/A' }}</span>
            </div>
            {% if project.completed_at %}
            <div style="display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--gray-100);">
                <span style="color: var(--gray-500);">Completed</span>
                <span>{{ project.completed_at[:10] }}</span>
            </div>
            {% endif %}
            <div style="display: flex; justify-content: space-between; padding: 8px 0;">
                <span style="color: var(--gray-500);">Color</span>
                <div style="width: 20px; height: 20px; border-radius: 4px; background: {{ project.color or '#6366F1' }};"></div>
            </div>
        </div>
    </div>
</main>

<style>
.checklist {
    display: flex;
    flex-direction: column;
}

.checklist-item {
    display: flex;
    align-items: center;
    padding: 12px 0;
    border-bottom: 1px solid var(--gray-100);
    gap: 12px;
}

.checklist-item:last-child {
    border-bottom: none;
}

.item-checkbox {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    border: 2px solid var(--gray-300);
    background: white;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    color: white;
    transition: all 0.2s;
    flex-shrink: 0;
}

.item-checkbox:hover {
    border-color: var(--success);
    background: rgba(16, 185, 129, 0.1);
}

.item-checkbox.checked {
    background: var(--success);
    border-color: var(--success);
}

.checklist-item.completed .item-text {
    text-decoration: line-through;
    color: var(--gray-400);
}

.item-text {
    flex: 1;
    color: var(--gray-700);
}
</style>
{% endblock %}
"""

PROJECT_CREATE_TEMPLATE = """
{% extends "base" %}
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
        <a href="{{ url_for('projects') }}" class="btn btn-secondary btn-sm">← Back to Projects</a>
    </div>
</nav>

<main class="main" style="max-width: 600px;">
    <div class="card">
        <div class="card-header">
            <h2 class="card-title">Create New Project</h2>
        </div>
        <form method="POST" class="card-body">
            <div class="form-group">
                <label class="form-label">Project Name *</label>
                <input type="text" name="name" class="form-input" placeholder="Website Redesign" required>
            </div>

            <div class="form-group">
                <label class="form-label">Description</label>
                <textarea name="description" class="form-input" rows="3" placeholder="Project goals and details..."></textarea>
            </div>

            <div class="form-group">
                <label class="form-label">Color</label>
                <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                    <label class="color-option">
                        <input type="radio" name="color" value="#6366F1" checked>
                        <span style="background: #6366F1;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#8B5CF6">
                        <span style="background: #8B5CF6;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#EC4899">
                        <span style="background: #EC4899;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#EF4444">
                        <span style="background: #EF4444;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#F59E0B">
                        <span style="background: #F59E0B;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#10B981">
                        <span style="background: #10B981;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#3B82F6">
                        <span style="background: #3B82F6;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#6B7280">
                        <span style="background: #6B7280;"></span>
                    </label>
                </div>
            </div>

            <div class="form-group">
                <label class="form-label">Initial Checklist Items (optional)</label>
                <textarea name="initial_items" class="form-input" rows="4" placeholder="One item per line:&#10;Design mockups&#10;Build frontend&#10;Test and deploy"></textarea>
                <small style="color: var(--gray-500);">Enter one item per line</small>
            </div>

            <div style="display: flex; gap: 12px; margin-top: 24px;">
                <button type="submit" class="btn btn-primary" style="flex: 1;">Create Project</button>
                <a href="{{ url_for('projects') }}" class="btn btn-secondary">Cancel</a>
            </div>
        </form>
    </div>
</main>

<style>
.color-option {
    cursor: pointer;
}

.color-option input {
    display: none;
}

.color-option span {
    display: block;
    width: 36px;
    height: 36px;
    border-radius: 8px;
    border: 3px solid transparent;
    transition: all 0.2s;
}

.color-option input:checked + span {
    border-color: var(--gray-900);
    transform: scale(1.1);
}

.color-option:hover span {
    transform: scale(1.05);
}
</style>
{% endblock %}
"""

# ============================================
# ROUTES
# ============================================

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    # Show landing page for non-logged in users
    from templates import LANDING_TEMPLATE
    return render_template_string(LANDING_TEMPLATE)


@app.route('/pricing')
def pricing_page():
    from templates import PRICING_TEMPLATE
    from billing import PLANS

    current_plan = 'starter'
    subscription_status = 'none'

    if 'user_id' in session:
        user = supabase.table('users').select('subscription_tier, subscription_status').eq('id', session['user_id']).single().execute()
        if user.data:
            current_plan = user.data.get('subscription_tier', 'starter')
            subscription_status = user.data.get('subscription_status', 'none')

    return render_template_string(
        PRICING_TEMPLATE,
        title='Pricing',
        plans=PLANS,
        current_plan=current_plan,
        subscription_status=subscription_status,
        **{'base': BASE_TEMPLATE}
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').lower()
        password = request.form.get('password', '')

        try:
            auth_response = supabase.auth.sign_in_with_password({
                'email': email,
                'password': password
            })

            if auth_response.user:
                session['user_id'] = auth_response.user.id
                session['user_email'] = auth_response.user.email

                # Get user profile
                user = supabase.table('users').select('*').eq('id', auth_response.user.id).single().execute()
                if user.data:
                    session['user_name'] = user.data.get('full_name', email.split('@')[0])
                    session['timezone'] = user.data.get('timezone', 'Australia/Brisbane')
                else:
                    session['user_name'] = email.split('@')[0]
                    session['timezone'] = 'Australia/Brisbane'

                return redirect(url_for('dashboard'))
            else:
                return render_template_string(LOGIN_TEMPLATE, title='Login', error='Invalid credentials', **{'base': BASE_TEMPLATE})

        except Exception as e:
            error_msg = 'Invalid email or password'
            return render_template_string(LOGIN_TEMPLATE, title='Login', error=error_msg, **{'base': BASE_TEMPLATE})

    return render_template_string(LOGIN_TEMPLATE, title='Login', error=None, **{'base': BASE_TEMPLATE})


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email', '').lower()
        password = request.form.get('password', '')
        full_name = request.form.get('full_name', '')
        timezone = request.form.get('timezone', 'Australia/Brisbane')

        try:
            # Create auth user
            auth_response = supabase.auth.sign_up({
                'email': email,
                'password': password
            })

            if auth_response.user:
                # Create user profile
                supabase.table('users').insert({
                    'id': auth_response.user.id,
                    'email': email,
                    'full_name': full_name,
                    'timezone': timezone,
                    'subscription_status': 'trial',
                    'subscription_tier': 'starter'
                }).execute()

                # Log them in
                session['user_id'] = auth_response.user.id
                session['user_email'] = email
                session['user_name'] = full_name
                session['timezone'] = timezone

                return redirect(url_for('dashboard'))

        except Exception as e:
            error_msg = str(e)
            if 'already registered' in error_msg.lower():
                error_msg = 'Email already registered'
            return render_template_string(SIGNUP_TEMPLATE, title='Sign Up', error=error_msg, **{'base': BASE_TEMPLATE})

    return render_template_string(SIGNUP_TEMPLATE, title='Sign Up', error=None, **{'base': BASE_TEMPLATE})


@app.route('/logout')
def logout():
    try:
        supabase.auth.sign_out()
    except:
        pass
    session.clear()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    tz = get_user_timezone()
    today = datetime.now(tz).date().isoformat()

    # Get tasks
    tasks = supabase.table('tasks')\
        .select('*')\
        .eq('user_id', user_id)\
        .order('due_date')\
        .order('due_time')\
        .limit(100)\
        .execute()

    # Calculate stats
    all_tasks = tasks.data or []
    pending_tasks = [t for t in all_tasks if t['status'] == 'pending']
    completed_tasks = [t for t in all_tasks if t['status'] == 'completed']

    stats = {
        'pending': len(pending_tasks),
        'due_today': len([t for t in pending_tasks if t.get('due_date') == today]),
        'overdue': len([t for t in pending_tasks if t.get('due_date') and t['due_date'] < today]),
        'completed_this_week': len([t for t in completed_tasks if t.get('completed_at')])  # Simplified
    }

    return render_template_string(
        DASHBOARD_TEMPLATE,
        title='Dashboard',
        tasks=all_tasks,
        stats=stats,
        today=today,
        **{'base': BASE_TEMPLATE}
    )


@app.route('/tasks/create', methods=['POST'])
@login_required
def create_task():
    user_id = session['user_id']

    task_data = {
        'user_id': user_id,
        'title': request.form.get('title'),
        'description': request.form.get('description'),
        'due_date': request.form.get('due_date'),
        'due_time': request.form.get('due_time', '09:00') + ':00',
        'priority': request.form.get('priority', 'medium'),
        'status': 'pending',
        'client_name': request.form.get('client_name') or None
    }

    supabase.table('tasks').insert(task_data).execute()
    return redirect(url_for('dashboard'))


@app.route('/tasks/<task_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_task(task_id):
    from templates import TASK_EDIT_TEMPLATE
    user_id = session['user_id']

    # Verify ownership
    task = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user_id).single().execute()
    if not task.data:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        update_data = {
            'title': request.form.get('title'),
            'description': request.form.get('description'),
            'due_date': request.form.get('due_date'),
            'due_time': request.form.get('due_time', '09:00') + ':00',
            'priority': request.form.get('priority'),
            'status': request.form.get('status'),
            'client_name': request.form.get('client_name') or None,
            'client_email': request.form.get('client_email') or None,
            'client_phone': request.form.get('client_phone') or None,
            'project_name': request.form.get('project_name') or None
        }

        supabase.table('tasks').update(update_data).eq('id', task_id).execute()
        return redirect(url_for('dashboard'))

    return render_template_string(
        TASK_EDIT_TEMPLATE,
        title='Edit Task',
        task=task.data,
        **{'base': BASE_TEMPLATE}
    )


@app.route('/tasks/<task_id>')
@login_required
def task_detail(task_id):
    from templates import TASK_DETAIL_TEMPLATE
    user_id = session['user_id']

    # Get task
    task = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user_id).single().execute()
    if not task.data:
        return redirect(url_for('dashboard'))

    # Get checklist items
    checklist = supabase.table('task_checklist_items')\
        .select('*')\
        .eq('task_id', task_id)\
        .order('display_order')\
        .execute()

    # Get notes
    notes = supabase.table('task_notes')\
        .select('*')\
        .eq('task_id', task_id)\
        .order('created_at', desc=True)\
        .limit(20)\
        .execute()

    return render_template_string(
        TASK_DETAIL_TEMPLATE,
        title=task.data['title'],
        task=task.data,
        checklist=checklist.data or [],
        notes=notes.data or [],
        **{'base': BASE_TEMPLATE}
    )


@app.route('/tasks/<task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    user_id = session['user_id']

    # Verify ownership and delete
    supabase.table('tasks').delete().eq('id', task_id).eq('user_id', user_id).execute()
    return redirect(url_for('dashboard'))


@app.route('/tasks/<task_id>/complete', methods=['POST'])
@login_required
def complete_task_action(task_id):
    user_id = session['user_id']

    supabase.table('tasks').update({
        'status': 'completed',
        'completed_at': datetime.now(pytz.UTC).isoformat()
    }).eq('id', task_id).eq('user_id', user_id).execute()

    return redirect(url_for('task_detail', task_id=task_id))


@app.route('/tasks/<task_id>/reopen', methods=['POST'])
@login_required
def reopen_task(task_id):
    user_id = session['user_id']

    supabase.table('tasks').update({
        'status': 'pending',
        'completed_at': None
    }).eq('id', task_id).eq('user_id', user_id).execute()

    return redirect(url_for('task_detail', task_id=task_id))


@app.route('/tasks/<task_id>/checklist', methods=['POST'])
@login_required
def update_checklist(task_id):
    user_id = session['user_id']
    completed_ids = request.form.getlist('completed')

    # Verify task ownership
    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return redirect(url_for('dashboard'))

    # Get all checklist items
    items = supabase.table('task_checklist_items').select('id').eq('task_id', task_id).execute()

    for item in items.data or []:
        is_completed = item['id'] in completed_ids
        update_data = {'is_completed': is_completed}
        if is_completed:
            update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
        else:
            update_data['completed_at'] = None

        supabase.table('task_checklist_items').update(update_data).eq('id', item['id']).execute()

    return redirect(url_for('task_detail', task_id=task_id))


@app.route('/tasks/<task_id>/checklist/add', methods=['POST'])
@login_required
def add_checklist_item(task_id):
    user_id = session['user_id']
    item_text = request.form.get('item_text', '').strip()

    if not item_text:
        return redirect(url_for('task_detail', task_id=task_id))

    # Verify ownership
    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return redirect(url_for('dashboard'))

    # Get max display order
    existing = supabase.table('task_checklist_items')\
        .select('display_order')\
        .eq('task_id', task_id)\
        .order('display_order', desc=True)\
        .limit(1)\
        .execute()

    max_order = existing.data[0]['display_order'] if existing.data else 0

    supabase.table('task_checklist_items').insert({
        'task_id': task_id,
        'item_text': item_text,
        'is_completed': False,
        'display_order': max_order + 1
    }).execute()

    return redirect(url_for('task_detail', task_id=task_id))


@app.route('/tasks/<task_id>/notes/add', methods=['POST'])
@login_required
def add_note(task_id):
    user_id = session['user_id']
    content = request.form.get('content', '').strip()

    if not content:
        return redirect(url_for('task_detail', task_id=task_id))

    # Verify ownership
    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return redirect(url_for('dashboard'))

    supabase.table('task_notes').insert({
        'task_id': task_id,
        'content': content,
        'source': 'manual',
        'created_by': 'user'
    }).execute()

    return redirect(url_for('task_detail', task_id=task_id))


@app.route('/tasks/delay', methods=['POST'])
@login_required
def delay_task_custom():
    user_id = session['user_id']
    task_id = request.form.get('task_id')
    new_date = request.form.get('new_date')
    new_time = request.form.get('new_time')

    # Verify ownership
    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return redirect(url_for('dashboard'))

    supabase.table('tasks').update({
        'due_date': new_date,
        'due_time': new_time + ':00',
        'status': 'pending'
    }).eq('id', task_id).execute()

    return redirect(url_for('dashboard'))


@app.route('/settings')
@login_required
def settings():
    user_id = session['user_id']
    user = supabase.table('users').select('*').eq('id', user_id).single().execute()

    return render_template_string(
        SETTINGS_TEMPLATE,
        title='Settings',
        user=user.data,
        message=request.args.get('message'),
        **{'base': BASE_TEMPLATE}
    )


@app.route('/settings/profile', methods=['POST'])
@login_required
def update_profile():
    user_id = session['user_id']

    update_data = {
        'full_name': request.form.get('full_name'),
        'company_name': request.form.get('company_name'),
        'timezone': request.form.get('timezone')
    }

    supabase.table('users').update(update_data).eq('id', user_id).execute()

    # Update session
    session['user_name'] = update_data['full_name']
    session['timezone'] = update_data['timezone']

    return redirect(url_for('settings', message='Profile updated successfully'))


@app.route('/settings/summary', methods=['POST'])
@login_required
def update_summary_settings():
    user_id = session['user_id']

    update_data = {
        'daily_summary_enabled': 'daily_summary_enabled' in request.form,
        'daily_summary_time': request.form.get('daily_summary_time', '08:00:00')
    }

    supabase.table('users').update(update_data).eq('id', user_id).execute()

    return redirect(url_for('settings', message='Summary settings updated successfully'))


@app.route('/billing')
@login_required
def billing():
    # Placeholder for Stripe billing portal
    return redirect(url_for('settings'))


# ============================================
# PROJECT ROUTES
# ============================================

@app.route('/projects')
@login_required
def projects():
    user_id = session['user_id']
    filter_status = request.args.get('filter', 'active')

    # Build query
    query = supabase.table('saas_projects').select('*').eq('user_id', user_id)

    if filter_status != 'all':
        query = query.eq('status', filter_status)

    result = query.order('created_at', desc=True).execute()
    projects_list = result.data or []

    # Get item counts and progress for each project
    for project in projects_list:
        items_result = supabase.table('saas_project_items')\
            .select('id, is_completed')\
            .eq('project_id', project['id'])\
            .execute()
        items = items_result.data or []
        project['item_count'] = len(items)
        completed = len([i for i in items if i['is_completed']])
        project['progress'] = int((completed / len(items) * 100)) if items else 0

    # Calculate stats
    all_projects = supabase.table('saas_projects').select('id, status').eq('user_id', user_id).execute().data or []
    all_items = []
    for p in all_projects:
        items_result = supabase.table('saas_project_items').select('is_completed').eq('project_id', p['id']).execute()
        all_items.extend(items_result.data or [])

    stats = {
        'active': len([p for p in all_projects if p['status'] == 'active']),
        'completed_projects': len([p for p in all_projects if p['status'] == 'completed']),
        'total_items': len(all_items),
        'completed_items': len([i for i in all_items if i['is_completed']])
    }

    return render_template_string(
        PROJECTS_TEMPLATE,
        title='Projects',
        projects=projects_list,
        stats=stats,
        filter=filter_status,
        **{'base': BASE_TEMPLATE}
    )


@app.route('/projects/create', methods=['GET', 'POST'])
@login_required
def project_create():
    user_id = session['user_id']

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        color = request.form.get('color', '#6366F1')
        initial_items = request.form.get('initial_items', '').strip()

        if not name:
            return redirect(url_for('project_create'))

        # Create project
        project_result = supabase.table('saas_projects').insert({
            'user_id': user_id,
            'name': name,
            'description': description or None,
            'color': color,
            'status': 'active'
        }).execute()

        if project_result.data:
            project_id = project_result.data[0]['id']

            # Add initial items if provided
            if initial_items:
                lines = [line.strip() for line in initial_items.split('\n') if line.strip()]
                for idx, item_text in enumerate(lines):
                    supabase.table('saas_project_items').insert({
                        'project_id': project_id,
                        'item_text': item_text,
                        'display_order': idx,
                        'source': 'manual'
                    }).execute()

            return redirect(url_for('project_detail', project_id=project_id))

        return redirect(url_for('projects'))

    return render_template_string(
        PROJECT_CREATE_TEMPLATE,
        title='Create Project',
        **{'base': BASE_TEMPLATE}
    )


@app.route('/projects/<project_id>')
@login_required
def project_detail(project_id):
    user_id = session['user_id']

    # Get project
    project = supabase.table('saas_projects')\
        .select('*')\
        .eq('id', project_id)\
        .eq('user_id', user_id)\
        .single()\
        .execute()

    if not project.data:
        return redirect(url_for('projects'))

    # Get items
    items = supabase.table('saas_project_items')\
        .select('*')\
        .eq('project_id', project_id)\
        .order('display_order')\
        .execute()

    items_list = items.data or []
    total_count = len(items_list)
    completed_count = len([i for i in items_list if i['is_completed']])
    progress = int((completed_count / total_count * 100)) if total_count else 0

    return render_template_string(
        PROJECT_DETAIL_TEMPLATE,
        title=project.data['name'],
        project=project.data,
        items=items_list,
        total_count=total_count,
        completed_count=completed_count,
        progress=progress,
        **{'base': BASE_TEMPLATE}
    )


@app.route('/projects/<project_id>/items/add', methods=['POST'])
@login_required
def project_item_add(project_id):
    user_id = session['user_id']
    item_text = request.form.get('item_text', '').strip()

    if not item_text:
        return redirect(url_for('project_detail', project_id=project_id))

    # Verify ownership
    project = supabase.table('saas_projects')\
        .select('id')\
        .eq('id', project_id)\
        .eq('user_id', user_id)\
        .execute()

    if not project.data:
        return redirect(url_for('projects'))

    # Get max display order
    existing = supabase.table('saas_project_items')\
        .select('display_order')\
        .eq('project_id', project_id)\
        .order('display_order', desc=True)\
        .limit(1)\
        .execute()

    max_order = existing.data[0]['display_order'] if existing.data else 0

    supabase.table('saas_project_items').insert({
        'project_id': project_id,
        'item_text': item_text,
        'display_order': max_order + 1,
        'source': 'manual'
    }).execute()

    return redirect(url_for('project_detail', project_id=project_id))


@app.route('/projects/<project_id>/items/<item_id>/toggle', methods=['POST'])
@login_required
def project_item_toggle(project_id, item_id):
    user_id = session['user_id']

    # Verify ownership
    project = supabase.table('saas_projects')\
        .select('id')\
        .eq('id', project_id)\
        .eq('user_id', user_id)\
        .execute()

    if not project.data:
        return redirect(url_for('projects'))

    # Get current state
    item = supabase.table('saas_project_items')\
        .select('is_completed')\
        .eq('id', item_id)\
        .eq('project_id', project_id)\
        .single()\
        .execute()

    if item.data:
        new_state = not item.data['is_completed']
        update_data = {'is_completed': new_state}
        if new_state:
            update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
        else:
            update_data['completed_at'] = None

        supabase.table('saas_project_items')\
            .update(update_data)\
            .eq('id', item_id)\
            .execute()

    return redirect(url_for('project_detail', project_id=project_id))


@app.route('/projects/<project_id>/complete', methods=['POST'])
@login_required
def project_complete(project_id):
    user_id = session['user_id']

    supabase.table('saas_projects').update({
        'status': 'completed',
        'completed_at': datetime.now(pytz.UTC).isoformat()
    }).eq('id', project_id).eq('user_id', user_id).execute()

    return redirect(url_for('project_detail', project_id=project_id))


@app.route('/projects/<project_id>/reopen', methods=['POST'])
@login_required
def project_reopen(project_id):
    user_id = session['user_id']

    supabase.table('saas_projects').update({
        'status': 'active',
        'completed_at': None
    }).eq('id', project_id).eq('user_id', user_id).execute()

    return redirect(url_for('project_detail', project_id=project_id))


@app.route('/projects/<project_id>/delete', methods=['POST'])
@login_required
def project_delete(project_id):
    user_id = session['user_id']

    # Delete will cascade to items due to FK constraint
    supabase.table('saas_projects')\
        .delete()\
        .eq('id', project_id)\
        .eq('user_id', user_id)\
        .execute()

    return redirect(url_for('projects'))


@app.route('/api/projects/<project_id>/items/<item_id>/toggle', methods=['POST'])
@login_required
def api_project_item_toggle(project_id, item_id):
    user_id = session['user_id']

    # Verify ownership
    project = supabase.table('saas_projects')\
        .select('id')\
        .eq('id', project_id)\
        .eq('user_id', user_id)\
        .execute()

    if not project.data:
        return jsonify({'error': 'Not found'}), 404

    # Get current state
    item = supabase.table('saas_project_items')\
        .select('is_completed')\
        .eq('id', item_id)\
        .eq('project_id', project_id)\
        .single()\
        .execute()

    if not item.data:
        return jsonify({'error': 'Item not found'}), 404

    new_state = not item.data['is_completed']
    update_data = {'is_completed': new_state}
    if new_state:
        update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
    else:
        update_data['completed_at'] = None

    supabase.table('saas_project_items')\
        .update(update_data)\
        .eq('id', item_id)\
        .execute()

    return jsonify({'success': True, 'is_completed': new_state})


# ============================================
# API ENDPOINTS
# ============================================

@app.route('/api/tasks/<task_id>/status', methods=['POST'])
@login_required
def api_update_task_status(task_id):
    user_id = session['user_id']
    data = request.get_json()
    new_status = data.get('status')

    # Verify ownership
    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return jsonify({'error': 'Not found'}), 404

    update_data = {'status': new_status}
    if new_status == 'completed':
        update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()

    supabase.table('tasks').update(update_data).eq('id', task_id).execute()
    return jsonify({'success': True})


@app.route('/api/tasks/<task_id>/delay', methods=['POST'])
@login_required
def api_delay_task(task_id):
    user_id = session['user_id']
    data = request.get_json()
    hours = data.get('hours', 0)
    days = data.get('days', 0)

    # Verify ownership and get task
    task = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user_id).single().execute()
    if not task.data:
        return jsonify({'error': 'Not found'}), 404

    tz = get_user_timezone()
    now = datetime.now(tz)
    new_dt = now + timedelta(hours=hours, days=days)

    supabase.table('tasks').update({
        'due_date': new_dt.date().isoformat(),
        'due_time': new_dt.strftime('%H:%M:%S'),
        'status': 'pending'
    }).eq('id', task_id).execute()

    return jsonify({'success': True, 'new_due': new_dt.isoformat()})


# ============================================
# MAIN
# ============================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
