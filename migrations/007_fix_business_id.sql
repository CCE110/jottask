-- Fix business_id constraint for email-created tasks
-- Run this in Supabase SQL Editor

-- Make business_id nullable (tasks now use user_id as primary ownership)
ALTER TABLE tasks ALTER COLUMN business_id DROP NOT NULL;
