-- Support Chat System for Jottask
-- Run this in Supabase SQL Editor

-- Support conversations table
CREATE TABLE IF NOT EXISTS support_conversations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    subject VARCHAR(255),
    status VARCHAR(20) DEFAULT 'open', -- open, escalated, resolved
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    escalated_at TIMESTAMP WITH TIME ZONE,
    resolved_at TIMESTAMP WITH TIME ZONE
);

-- Support messages table
CREATE TABLE IF NOT EXISTS support_messages (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    conversation_id UUID REFERENCES support_conversations(id) ON DELETE CASCADE,
    sender_type VARCHAR(20) NOT NULL, -- 'user', 'bot', 'admin'
    message TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_support_conversations_user ON support_conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_support_conversations_status ON support_conversations(status);
CREATE INDEX IF NOT EXISTS idx_support_messages_conversation ON support_messages(conversation_id);

-- Disable RLS for admin access (using service role key)
ALTER TABLE support_conversations DISABLE ROW LEVEL SECURITY;
ALTER TABLE support_messages DISABLE ROW LEVEL SECURITY;
