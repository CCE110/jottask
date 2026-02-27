-- Migration 021: Chat conversations and messages for AI chat interface
-- Run in Supabase SQL Editor

-- Chat conversations
CREATE TABLE chat_conversations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255) DEFAULT 'New Chat',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Chat messages
CREATE TABLE chat_messages (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,        -- 'user', 'assistant', 'tool_result'
    content TEXT,
    tool_calls JSONB,                 -- Assistant's tool_use blocks
    tool_results JSONB,               -- Tool execution results
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_chat_conversations_user ON chat_conversations(user_id, updated_at DESC);
CREATE INDEX idx_chat_messages_conversation ON chat_messages(conversation_id, created_at);

-- RLS policies
ALTER TABLE chat_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;

-- Users can only see their own conversations
CREATE POLICY "Users can view own conversations"
    ON chat_conversations FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can insert own conversations"
    ON chat_conversations FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update own conversations"
    ON chat_conversations FOR UPDATE
    USING (user_id = auth.uid());

CREATE POLICY "Users can delete own conversations"
    ON chat_conversations FOR DELETE
    USING (user_id = auth.uid());

-- Messages: users can access messages in their conversations
CREATE POLICY "Users can view messages in own conversations"
    ON chat_messages FOR SELECT
    USING (conversation_id IN (
        SELECT id FROM chat_conversations WHERE user_id = auth.uid()
    ));

CREATE POLICY "Users can insert messages in own conversations"
    ON chat_messages FOR INSERT
    WITH CHECK (conversation_id IN (
        SELECT id FROM chat_conversations WHERE user_id = auth.uid()
    ));

-- Service role bypass (for server-side operations)
CREATE POLICY "Service role full access conversations"
    ON chat_conversations FOR ALL
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Service role full access messages"
    ON chat_messages FOR ALL
    USING (true)
    WITH CHECK (true);
