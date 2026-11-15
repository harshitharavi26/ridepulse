-- Apply once with ON_ERROR_STOP to canonical databases. Does not rewrite facts.
-- Statement triggers see only actual changed rows, including ON CONFLICT behavior.
BEGIN;
SET LOCAL lock_timeout = '10s';

CREATE TABLE IF NOT EXISTS dataset_versions (
    city TEXT NOT NULL,
    domain TEXT NOT NULL CHECK (domain IN ('trips', 'metadata', 'availability')),
    version BIGINT NOT NULL DEFAULT 0,
    committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (city, domain)
);

INSERT INTO dataset_versions(city, domain)
SELECT city, domain FROM (VALUES ('nyc'), ('boulder')) AS cities(city)
CROSS JOIN (VALUES ('trips'), ('metadata'), ('availability')) AS domains(domain)
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION ridepulse_advance_version() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
DECLARE
    changed_cities TEXT[];
    changed_city TEXT;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        -- Administrative resets must invalidate previous cached results too.
        SELECT array_agg(city ORDER BY city) INTO changed_cities FROM dataset_versions WHERE domain = TG_ARGV[0];
    ELSIF TG_OP = 'INSERT' THEN
        SELECT array_agg(DISTINCT city ORDER BY city) INTO changed_cities FROM new_rows;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT array_agg(DISTINCT city ORDER BY city) INTO changed_cities FROM old_rows;
    ELSE
        -- Ignore identical UPDATEs (e.g. replay of equal-timestamp metadata).
        SELECT array_agg(DISTINCT city ORDER BY city) INTO changed_cities FROM (
            (SELECT * FROM new_rows EXCEPT SELECT * FROM old_rows)
            UNION ALL
            (SELECT * FROM old_rows EXCEPT SELECT * FROM new_rows)
        ) changed;
    END IF;
    IF changed_cities IS NOT NULL THEN
        FOREACH changed_city IN ARRAY changed_cities LOOP
            INSERT INTO dataset_versions(city, domain, version, committed_at)
            VALUES (changed_city, TG_ARGV[0], 1, clock_timestamp())
            ON CONFLICT (city, domain) DO UPDATE
            SET version = dataset_versions.version + 1, committed_at = clock_timestamp();
        END LOOP;
    END IF;
    RETURN NULL;
END;
$$;

-- The application role already has SELECT/INSERT/UPDATE on existing fact tables.
-- Invoker-security triggers intentionally require explicit version-table grants.
DO $$
DECLARE
    table_name TEXT;
    domain_name TEXT;
BEGIN
    FOR table_name, domain_name IN
        SELECT * FROM (VALUES ('trips','trips'), ('stations','metadata'),
                              ('station_snapshots','availability')) entries(t,d)
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS ridepulse_version_insert ON %I', table_name);
        EXECUTE format('DROP TRIGGER IF EXISTS ridepulse_version_update ON %I', table_name);
        EXECUTE format('DROP TRIGGER IF EXISTS ridepulse_version_delete ON %I', table_name);
        EXECUTE format('DROP TRIGGER IF EXISTS ridepulse_version_truncate ON %I', table_name);
        EXECUTE format('CREATE TRIGGER ridepulse_version_insert AFTER INSERT ON %I REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ridepulse_advance_version(%L)', table_name, domain_name);
        EXECUTE format('CREATE TRIGGER ridepulse_version_update AFTER UPDATE ON %I REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION ridepulse_advance_version(%L)', table_name, domain_name);
        EXECUTE format('CREATE TRIGGER ridepulse_version_delete AFTER DELETE ON %I REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION ridepulse_advance_version(%L)', table_name, domain_name);
        EXECUTE format('CREATE TRIGGER ridepulse_version_truncate AFTER TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION ridepulse_advance_version(%L)', table_name, domain_name);
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ridepulse_app') THEN
        GRANT SELECT, INSERT, UPDATE ON dataset_versions TO ridepulse_app;
    END IF;
END;
$$;
COMMIT;
