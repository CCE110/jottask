-- Migration 025: Add phone number to squad_parent_links
-- Safe to run on live DB — idempotent

ALTER TABLE public.squad_parent_links ADD COLUMN IF NOT EXISTS parent_phone TEXT;
