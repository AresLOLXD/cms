# Spec Delta

## Purpose

Bounds how long any CMS database connection waits for a lock, lets the
operator override or switch off that bound from the database URL or the
environment, and keeps a lock timeout in EvaluationService a quiet, retried
event instead of an error with a traceback.

## ADDED Requirements

### Requirement: Every CMS connection waits at most 30 seconds for a lock
Every connection CMS opens to PostgreSQL SHALL start with a session
`lock_timeout` of 30 seconds, whatever path opens it: the sync engine, the
async engine, a standalone psycopg2 connection, a pooled connection used for a
large object, and a green (gevent) connection. A statement that waits longer
than the bound for a lock SHALL fail with SQLSTATE 55P03, which is not a lost
connection.

#### Scenario: Every connection path starts with a 30 s lock timeout
- **WHEN** CMS opens a connection through the sync engine, the async engine, a standalone psycopg2 connection, a pooled large-object connection or a green connection, with no override
- **THEN** `SHOW lock_timeout` on that connection returns `30s`

#### Scenario: A lock wait longer than the bound fails with SQLSTATE 55P03
- **GIVEN** another session holds a lock and the bound of a CMS connection is 1 second
- **WHEN** that connection asks for the same lock
- **THEN** the statement fails with SQLSTATE 55P03 after about 1 second
- **AND** the error is not reported as a lost connection

### Requirement: The URL's options and PGOPTIONS override the lock timeout
CMS SHALL merge the `PGOPTIONS` environment variable and the `options` of the
database URL into the options it sends, after its own default, so that a later
setting wins: the default, then `PGOPTIONS`, then the URL. Neither SHALL be
dropped because CMS sets options of its own.

#### Scenario: PGOPTIONS is merged, not dropped
- **GIVEN** `PGOPTIONS` sets another parameter and the URL has no `options`
- **WHEN** CMS opens a connection
- **THEN** the connection has both the 30 s lock timeout and the parameter from `PGOPTIONS`

#### Scenario: The URL's options beat PGOPTIONS and the default
- **GIVEN** `PGOPTIONS` and the URL's `options` both set `lock_timeout`, to different values
- **WHEN** CMS opens a connection
- **THEN** the connection's `lock_timeout` is the URL's value

### Requirement: lock_timeout=0 in the URL switches the bound off
Adding `options=-c lock_timeout=0` (percent-encoded in the URL) to the database
URL SHALL switch the lock bound off for every connection path, so that CMS
waits for locks without a limit, as before this change.

#### Scenario: lock_timeout=0 in the URL switches the bound off
- **WHEN** the database URL carries the runbook's kill-switch suffix, which includes `options=-c%20lock_timeout%3D0`
- **THEN** `SHOW lock_timeout` returns `0` on the sync engine, the async engine and a standalone psycopg2 connection

### Requirement: The fork's schema updates keep their 5-second lock bound
A transaction that sets its own `lock_timeout` with `SET LOCAL` SHALL keep that
value over the session's 30 s default: the fork's schema updates still give up
on a locked table after 5 seconds.

#### Scenario: The 5 s DDL bound wins over the 30 s session default
- **GIVEN** another session holds a lock on a table that a fork schema update needs, and the update's connection has the 30 s session default
- **WHEN** the schema update runs
- **THEN** it gives up after about 5 seconds, not 30, with its usual message

### Requirement: EvaluationService reports a lock timeout on a result as a warning
When writing a worker result fails with a lock timeout (SQLSTATE 55P03),
EvaluationService SHALL log one WARNING without a traceback, skip the other
results of the same object in that write, write the rest of the batch, and
leave the skipped results to the sweeper.

#### Scenario: A lock timeout on a result row is a warning, not a traceback
- **GIVEN** a write of worker results for two submissions
- **WHEN** the first result of the first submission fails with SQLSTATE 55P03
- **THEN** EvaluationService logs one WARNING without a traceback and no ERROR
- **AND** the other results of the first submission are not attempted in that write
- **AND** the results of the second submission are written
