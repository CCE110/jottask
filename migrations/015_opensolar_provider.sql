-- Migration 015: Add OpenSolar as a CRM connection provider
-- Run in Supabase SQL Editor

-- Update the provider check constraint to include 'opensolar'
ALTER TABLE crm_connections DROP CONSTRAINT IF EXISTS crm_connections_provider_check;
ALTER TABLE crm_connections ADD CONSTRAINT crm_connections_provider_check
    CHECK (provider IN ('pipereply', 'hubspot', 'zoho', 'salesforce', 'opensolar', 'none'));
