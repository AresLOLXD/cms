-- Reads only: the one thing it creates are two TEMP tables of this session
-- (dropped at COMMIT/disconnect); a READ ONLY transaction cannot create even those.
-- Run: psql "<url>" -X -f measure.sql   (one transaction, nothing written)
-- Part A needs only the cms role. Parts B and C read pg_largeobject, which
-- needs a superuser or a member of pg_read_all_data (PostgreSQL 14+);
-- they scan every large object page once (minutes on many GB): run them
-- off-peak or on a restored copy.
BEGIN;
SET LOCAL statement_timeout = 0;

\echo === A1. Size of every table (heap + TOAST + indexes), biggest first
SELECT c.relname AS table_name,
       c.reltuples::bigint AS approx_rows,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
       pg_size_pretty(pg_relation_size(c.oid)) AS heap,
       pg_size_pretty(pg_indexes_size(c.oid)) AS indexes,
       pg_total_relation_size(c.oid) AS total_bytes
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname IN ('public', 'pg_catalog')
  AND (n.nspname = 'public' OR c.relname IN ('pg_largeobject'))
ORDER BY total_bytes DESC;

\echo === A2. Whole database and the share of its large objects
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_total,
       pg_size_pretty(pg_total_relation_size('pg_largeobject')) AS large_objects,
       (SELECT count(*) FROM pg_largeobject_metadata) AS large_object_count,
       (SELECT count(*) FROM fsobjects) AS fsobject_rows;

\echo === A3. Ordinary-table weight per contest (bytes of the rows, not indexes)
WITH sub AS (
  SELECT s.id AS submission_id, t.contest_id
  FROM submissions s JOIN tasks t ON t.id = s.task_id)
SELECT c.id AS contest_id, c.name,
  (SELECT count(*) FROM sub WHERE sub.contest_id = c.id) AS submissions,
  (SELECT count(*) FROM submission_results r JOIN sub USING (submission_id)
     WHERE sub.contest_id = c.id) AS submission_results,
  (SELECT count(*) FROM evaluations e JOIN sub USING (submission_id)
     WHERE sub.contest_id = c.id) AS evaluations,
  pg_size_pretty((SELECT coalesce(sum(pg_column_size(e.*)), 0)
     FROM evaluations e JOIN sub USING (submission_id)
     WHERE sub.contest_id = c.id)) AS evaluations_bytes,
  pg_size_pretty((SELECT coalesce(sum(pg_column_size(r.*)), 0)
     FROM submission_results r JOIN sub USING (submission_id)
     WHERE sub.contest_id = c.id)) AS submission_results_bytes,
  (SELECT count(*) FROM activity_intervals a JOIN participations p
     ON p.id = a.participation_id WHERE p.contest_id = c.id) AS activity_intervals,
  (SELECT count(*) FROM user_tests u JOIN tasks t ON t.id = u.task_id
     WHERE t.contest_id = c.id) AS user_tests
FROM contests c ORDER BY c.id;

\echo === B. Large objects by kind (a digest used in two kinds counts in both)
CREATE TEMP TABLE refs ON COMMIT DROP AS
SELECT DISTINCT digest::text AS digest, kind, coalesce(contest_id, -1) AS contest_id FROM (
  SELECT st.digest, 'statement' AS kind, t.contest_id FROM statements st JOIN tasks t ON t.id = st.task_id
  UNION ALL SELECT a.digest, 'attachment', t.contest_id FROM attachments a JOIN tasks t ON t.id = a.task_id
  UNION ALL SELECT m.digest, 'manager', t.contest_id FROM managers m JOIN datasets d ON d.id = m.dataset_id JOIN tasks t ON t.id = d.task_id
  UNION ALL SELECT tc.input, 'testcase input', t.contest_id FROM testcases tc JOIN datasets d ON d.id = tc.dataset_id JOIN tasks t ON t.id = d.task_id
  UNION ALL SELECT tc.output, 'testcase output', t.contest_id FROM testcases tc JOIN datasets d ON d.id = tc.dataset_id JOIN tasks t ON t.id = d.task_id
  UNION ALL SELECT f.digest, 'submission source', t.contest_id FROM files f JOIN submissions s ON s.id = f.submission_id JOIN tasks t ON t.id = s.task_id
  UNION ALL SELECT e.digest, 'submission executable', t.contest_id FROM executables e JOIN submissions s ON s.id = e.submission_id JOIN tasks t ON t.id = s.task_id
  UNION ALL SELECT u.input, 'user test input', t.contest_id FROM user_tests u JOIN tasks t ON t.id = u.task_id
  UNION ALL SELECT f.digest, 'user test file', t.contest_id FROM user_test_files f JOIN user_tests u ON u.id = f.user_test_id JOIN tasks t ON t.id = u.task_id
  UNION ALL SELECT m.digest, 'user test manager', t.contest_id FROM user_test_managers m JOIN user_tests u ON u.id = m.user_test_id JOIN tasks t ON t.id = u.task_id
  UNION ALL SELECT e.digest, 'user test executable', t.contest_id FROM user_test_executables e JOIN user_tests u ON u.id = e.user_test_id JOIN tasks t ON t.id = u.task_id
  UNION ALL SELECT r.output, 'user test output', t.contest_id FROM user_test_results r JOIN user_tests u ON u.id = r.user_test_id JOIN tasks t ON t.id = u.task_id WHERE r.output IS NOT NULL
  UNION ALL SELECT unnest(r.compilation_sandbox_digests), 'sandbox archive', t.contest_id FROM submission_results r JOIN submissions s ON s.id = r.submission_id JOIN tasks t ON t.id = s.task_id
  UNION ALL SELECT unnest(e.evaluation_sandbox_digests), 'sandbox archive', t.contest_id FROM evaluations e JOIN submissions s ON s.id = e.submission_id JOIN tasks t ON t.id = s.task_id
  UNION ALL SELECT unnest(r.compilation_sandbox_digests || r.evaluation_sandbox_digests), 'sandbox archive', t.contest_id FROM user_test_results r JOIN user_tests u ON u.id = r.user_test_id JOIN tasks t ON t.id = u.task_id
) x;

CREATE TEMP TABLE lo_size ON COMMIT DROP AS
SELECT f.digest::text AS digest, f.loid, coalesce(sum(octet_length(l.data)), 0) AS bytes
FROM fsobjects f LEFT JOIN pg_largeobject l ON l.loid = f.loid
GROUP BY f.digest, f.loid;

SELECT r.kind, count(DISTINCT r.digest) AS files,
       pg_size_pretty(sum(s.bytes)) AS logical_size
FROM (SELECT DISTINCT digest, kind FROM refs) r JOIN lo_size s USING (digest)
GROUP BY r.kind ORDER BY sum(s.bytes) DESC;

\echo === B2. Not referenced by any row (this is what cmsCleanFiles would unlink), and the large objects that no fsobject names
SELECT 'fsobject without any reference' AS what, count(*) AS files,
       pg_size_pretty(coalesce(sum(bytes), 0)) AS logical_size
FROM lo_size WHERE digest NOT IN (SELECT digest FROM refs)
UNION ALL
SELECT 'large object without fsobject', count(*),
       pg_size_pretty(coalesce(sum(octet_length(l.data)), 0))
FROM pg_largeobject l WHERE l.loid NOT IN (SELECT loid FROM fsobjects);

\echo === C. Per contest: files it references, and what removing it would free (files no other contest or task shares)
WITH owners AS (
  SELECT digest, count(DISTINCT contest_id) AS n_contests FROM refs GROUP BY digest),
per AS (
  SELECT r.contest_id, r.digest, s.bytes, o.n_contests
  FROM (SELECT DISTINCT contest_id, digest FROM refs) r
  JOIN lo_size s USING (digest) JOIN owners o USING (digest))
SELECT p.contest_id, coalesce(c.name, '(task without contest)') AS contest,
       count(*) AS files,
       pg_size_pretty(sum(bytes)) AS referenced,
       pg_size_pretty(sum(bytes) FILTER (WHERE n_contests = 1)) AS freed_if_removed
FROM per p LEFT JOIN contests c ON c.id = p.contest_id
GROUP BY p.contest_id, c.name ORDER BY sum(bytes) DESC;

\echo === C2. Executables and user test outputs per contest
SELECT r.contest_id, coalesce(c.name, '(none)') AS contest, r.kind,
       count(DISTINCT r.digest) AS files, pg_size_pretty(sum(s.bytes)) AS logical_size
FROM refs r JOIN lo_size s USING (digest) LEFT JOIN contests c ON c.id = r.contest_id
WHERE r.kind IN ('submission executable', 'user test executable', 'user test output')
GROUP BY r.contest_id, c.name, r.kind ORDER BY r.contest_id, sum(s.bytes) DESC;
COMMIT;
