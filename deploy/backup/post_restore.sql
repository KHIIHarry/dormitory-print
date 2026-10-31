-- Post-restore security reset for DormPrint V1.
--
-- This runs after pg_restore and after apply_tombstones.py. It deliberately
-- cannot know whether the restored snapshot is the newest authority state:
-- the journal carries the authority manifest exported from the live database
-- at backup time, and restore.sh concatenates the external (newer) journal
-- before the snapshot journal. apply_tombstones.py records whether the
-- external manifest was provably fresher in
-- _dormprint_restore_authority.latest.
--
-- Fail-closed rule: without a fresher external manifest every staff account is
-- disabled, including super admins. A single snapshot manifest only describes
-- the state at backup time; an employee who was demoted or revoked after that
-- backup would otherwise regain old authority merely by logging in. A human
-- must review the restored authority table and bootstrap grants explicitly.
--
-- With a fresher external manifest, current staff/account status, role and
-- capabilities are overlaid. Super admins are still left disabled and require
-- manual bootstrap review. All sessions, access grants, one-time tokens,
-- idempotency records and protected email payloads are invalidated below.

BEGIN;

CREATE TABLE IF NOT EXISTS _dormprint_restore_authority (
    kind text NOT NULL,
    id text NOT NULL,
    facts jsonb NOT NULL,
    latest boolean NOT NULL DEFAULT false,
    PRIMARY KEY (kind, id)
);

-- A backup produced before this protocol has no authority row. Treat that as
-- "latest unknown" rather than letting the snapshot's staff state stay live.
INSERT INTO _dormprint_restore_authority (kind, id, facts, latest)
VALUES ('AUTHORITY', '__authority__', '{"staff": [], "accounts": []}'::jsonb, false)
ON CONFLICT (kind, id) DO NOTHING;

-- 1. Close every staff door first. The overlay below may reopen only
--    non-super staff facts from a manifest proven fresher.
UPDATE staff
SET enabled = false,
    status = 'DISABLED',
    capabilities = '[]'::jsonb;

-- 2. Overlay current authority only when apply_tombstones.py proved the
--    external journal is newer than the restored snapshot. Never reactivate a
--    super admin automatically.
UPDATE staff AS s
SET enabled = (f->>'enabled')::boolean,
    status = f->>'status',
    role = f->>'role',
    capabilities = COALESCE(f->'capabilities', '[]'::jsonb),
    session_version = (f->>'sessionVersion')::integer
FROM _dormprint_restore_authority AS a,
     jsonb_array_elements(a.facts->'staff') AS f
WHERE a.kind = 'AUTHORITY'
  AND a.id = '__authority__'
  AND a.latest
  AND (f->>'isSuperAdmin')::boolean = false
  AND s.id = f->>'id'
  AND s.is_super_admin = false;

-- 3. Accounts deleted or disabled after the backup have no fact in a fresher
--    manifest. Disable them rather than restoring a stale active account.
UPDATE customer_accounts AS c
SET status = 'DISABLED'
WHERE (SELECT a.latest FROM _dormprint_restore_authority AS a
       WHERE a.kind = 'AUTHORITY' AND a.id = '__authority__')
  AND NOT EXISTS (
      SELECT 1
      FROM _dormprint_restore_authority AS a,
           jsonb_array_elements(a.facts->'accounts') AS f
      WHERE a.kind = 'AUTHORITY'
        AND a.id = '__authority__'
        AND a.latest
        AND f->>'id' = c.id
  );

UPDATE customer_accounts AS c
SET status = f->>'status',
    session_version = (f->>'sessionVersion')::integer,
    deleted_at = NULLIF(f->>'deletedAt', '')::timestamptz
FROM _dormprint_restore_authority AS a,
     jsonb_array_elements(a.facts->'accounts') AS f
WHERE a.kind = 'AUTHORITY'
  AND a.id = '__authority__'
  AND a.latest
  AND c.id = f->>'id';

-- 4. Universal credential/session invalidation. This is intentionally
--    unconditional: role-grant rows in the restored database are not evidence
--    that a person still holds that role today.
UPDATE customer_accounts
SET session_version = session_version + 1;

UPDATE staff
SET session_version = session_version + 1;

UPDATE auth_sessions
SET revoked_at = now()
WHERE revoked_at IS NULL;

UPDATE order_access_grants
SET revoked_at = now()
WHERE revoked_at IS NULL;

DELETE FROM one_time_tokens;
DELETE FROM idempotency_records;

UPDATE jobs
SET status = 'QUEUED',
    lease_until = NULL,
    lease_token = NULL
WHERE status = 'PROCESSING';

-- 5. Protected email payloads are encrypted-at-rest data keyed by the old
--    deployment. A restored database must not be able to send them; remove the
--    protected body and common PII keys, and cancel queued mail.
UPDATE email_outbox
SET protected_payload = NULL,
    status = 'CANCELLED',
    data = COALESCE(data, '{}'::jsonb)
           - 'recipient' - 'email' - 'phone' - 'name'
           - 'subject' - 'body'
WHERE protected_payload IS NOT NULL
   OR status IN ('QUEUED', 'RETRY', 'RETRY_PENDING');

-- 6. Leave an auditable marker without copying PII or encrypted payloads.
INSERT INTO audit_logs
    (id, actor_id, action, target_type, target_id, reason, request_id, data,
     version, created_at, updated_at)
VALUES
    ('restore-security-reset', 'restore', 'RESTORE_SECURITY_RESET',
     'RESTORE', 'post_restore', 'fail-closed credential and authority reset', '',
     jsonb_build_object(
         'authorityLatest',
         COALESCE((SELECT latest FROM _dormprint_restore_authority
                   WHERE kind = 'AUTHORITY' AND id = '__authority__'), false)
     ),
     1, now(), now())
ON CONFLICT (id) DO NOTHING;

DO $$
DECLARE
    fresh boolean;
BEGIN
    SELECT latest INTO fresh
    FROM _dormprint_restore_authority
    WHERE kind = 'AUTHORITY' AND id = '__authority__';
    RAISE NOTICE 'RESTORE_AUTHORITY latest=% staff_disabled_until_review=true', COALESCE(fresh, false);
END
$$;

COMMIT;
