-- Migration 022: Referral invites tracking
-- Tracks emails invited by users before they sign up

CREATE TABLE IF NOT EXISTS referral_invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    invited_email TEXT NOT NULL,
    referral_code TEXT NOT NULL,
    status TEXT DEFAULT 'sent',  -- sent, signed_up, converted
    sent_at TIMESTAMPTZ DEFAULT NOW(),
    signed_up_at TIMESTAMPTZ,
    converted_at TIMESTAMPTZ
);

CREATE INDEX idx_referral_invites_referrer ON referral_invites(referrer_id);
CREATE INDEX idx_referral_invites_email ON referral_invites(invited_email);
CREATE UNIQUE INDEX idx_referral_invites_unique ON referral_invites(referrer_id, invited_email);

-- RLS
ALTER TABLE referral_invites ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own invites"
    ON referral_invites FOR SELECT
    USING (referrer_id = auth.uid());

CREATE POLICY "Users can insert own invites"
    ON referral_invites FOR INSERT
    WITH CHECK (referrer_id = auth.uid());

CREATE POLICY "Service role full access invites"
    ON referral_invites FOR ALL
    USING (true)
    WITH CHECK (true);
