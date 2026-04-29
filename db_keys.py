"""Resolve the Supabase service-role key with fallback to anon.

After migrations 029 + 030 enabled RLS on every public table, anon-key
clients are blocked from writes (and most reads). Long-running workers
and Flask routes need the service-role key to bypass RLS — that's the
intended design (we lock down anon, service trusts the app).

Single source of truth for picking the right key so workers and the web
process can't drift apart. Prefers SUPABASE_SERVICE_KEY (the project's
existing convention from squad_routes._admin_db) then
SUPABASE_SERVICE_ROLE_KEY (Supabase's official env-var name) before
falling back to SUPABASE_KEY (which is the anon key — fine for tests
and local dev, but blocks writes against the live RLS-hardened DB).
"""
import os


def get_admin_key() -> str:
    """Return the most-privileged Supabase key available in the env.

    Order: SUPABASE_SERVICE_KEY → SUPABASE_SERVICE_ROLE_KEY → SUPABASE_KEY.
    Empty string if none are set (caller should treat as misconfig).
    """
    return (
        os.getenv('SUPABASE_SERVICE_KEY')
        or os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        or os.getenv('SUPABASE_KEY')
        or ''
    )
