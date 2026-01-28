-- Subscriptions and Referrals System
-- Run this in Supabase SQL Editor

-- Add subscription fields to users table
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_tier TEXT DEFAULT 'free_trial';
ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tasks_this_month INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tasks_month_reset DATE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by UUID REFERENCES users(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_credits DECIMAL(10,2) DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;

-- Set trial end date for existing users (14 days from now)
UPDATE users SET trial_ends_at = NOW() + INTERVAL '14 days' WHERE trial_ends_at IS NULL;

-- Generate referral codes for existing users
UPDATE users SET referral_code = UPPER(SUBSTRING(MD5(id::text || 'jottask') FROM 1 FOR 8)) WHERE referral_code IS NULL;

-- Create referrals tracking table
CREATE TABLE IF NOT EXISTS referrals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    referred_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    referral_code TEXT NOT NULL,
    status TEXT DEFAULT 'pending', -- pending, trial, converted, expired
    reward_given BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    converted_at TIMESTAMPTZ,
    UNIQUE(referred_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code);
CREATE INDEX IF NOT EXISTS idx_users_subscription_tier ON users(subscription_tier);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id);
CREATE INDEX IF NOT EXISTS idx_referrals_status ON referrals(status);

-- Function to generate referral code for new users
CREATE OR REPLACE FUNCTION generate_referral_code()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.referral_code IS NULL THEN
        NEW.referral_code := UPPER(SUBSTRING(MD5(NEW.id::text || 'jottask' || EXTRACT(EPOCH FROM NOW())::text) FROM 1 FOR 8));
    END IF;
    IF NEW.trial_ends_at IS NULL THEN
        NEW.trial_ends_at := NOW() + INTERVAL '14 days';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for new users
DROP TRIGGER IF EXISTS trigger_generate_referral_code ON users;
CREATE TRIGGER trigger_generate_referral_code
    BEFORE INSERT ON users
    FOR EACH ROW
    EXECUTE FUNCTION generate_referral_code();
