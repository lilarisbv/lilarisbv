-- ============================================================
-- Neutralize: Reset go_live so the API stays blocked on the copy.
-- (is_neutralized is computed from core's database.is_neutralized flag.)
-- ============================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'mk_instance'
          AND column_name = 'go_live'
    ) THEN
        UPDATE mk_instance
        SET
            go_live = FALSE;
    END IF;
END $$;

-- ============================================================
-- Neutralize Webhooks: Deactivate all *.webhook.ts tables
-- ============================================================
DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name LIKE '%\_webhook\_ts' ESCAPE '\'
          AND column_name = 'active_webhook'
    LOOP
        EXECUTE format('UPDATE %I SET active_webhook = FALSE WHERE active_webhook = TRUE', rec.table_name);
    END LOOP;
END $$;