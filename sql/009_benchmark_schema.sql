-- Run as the database owner in ridepulse, alongside migration 008.
-- All experimental facts are isolated from public.trips and production analytics.
CREATE SCHEMA IF NOT EXISTS ridepulse_benchmark AUTHORIZATION ridepulse_app;
