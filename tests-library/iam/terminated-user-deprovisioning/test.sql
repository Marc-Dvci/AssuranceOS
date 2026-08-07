SELECT
  'IAM-01:' || t.user_id AS exception_key,
  'identity:' || t.user_id AS subject_ref,
  CASE
    WHEN e.active = 1 THEN 'approved_exception'
    WHEN a.user_id IS NULL THEN 'missing_directory_account'
    WHEN a.enabled = 1 THEN 'active_after_termination'
    WHEN a.disabled_at IS NULL OR a.disabled_at > t.disable_due_at THEN 'late_deprovisioning'
    ELSE 'effective'
  END AS classification,
  'high' AS severity,
  CASE WHEN e.active = 1 THEN 'approved_exception' ELSE 'open' END AS status,
  CASE
    WHEN e.active = 1 THEN 'active approved exception applies'
    WHEN a.user_id IS NULL THEN 'terminated user has no matched directory account'
    WHEN a.enabled = 1 THEN 'directory account remains enabled after termination'
    WHEN a.disabled_at IS NULL OR a.disabled_at > t.disable_due_at THEN 'directory account was disabled after the approved deadline'
    ELSE 'account disabled by deadline'
  END AS reason,
  CASE
    WHEN e.active = 1 THEN 0
    WHEN a.user_id IS NULL OR a.enabled = 1 OR a.disabled_at IS NULL OR a.disabled_at > t.disable_due_at THEN 1
    ELSE 0
  END AS is_exception,
  t.user_id,
  t.terminated_at,
  t.disable_due_at,
  a.enabled,
  a.disabled_at,
  t.evidence_id AS termination_evidence_id,
  a.evidence_id AS account_evidence_id
FROM terminated_users t
LEFT JOIN directory_accounts a ON a.user_id = t.user_id
LEFT JOIN approved_exceptions e ON e.exception_key = a.exception_key AND e.active = 1
ORDER BY t.user_id
