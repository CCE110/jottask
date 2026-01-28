-- Add alternate emails support for users
-- Run this in Supabase SQL Editor

-- Add alternate_emails column to users table
ALTER TABLE users ADD COLUMN IF NOT EXISTS alternate_emails TEXT[];

-- Set your alternate emails
UPDATE users
SET alternate_emails = ARRAY[
    'rob@aiprojectpro.com.au',
    'rob.l@directsolarwholesaler.com.au',
    'roblowe007@gmail.com',
    'rob@kvell.net'
]
WHERE email = 'rob@cloudcleanenergy.com.au';
