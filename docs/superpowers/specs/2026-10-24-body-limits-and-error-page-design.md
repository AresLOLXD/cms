# Bound request bodies in Tornado, and a specific CWS message for a rejected body (issues #65, #66) — Design

**Date:** 2026-10-24
**Status:** Decisions by a read-only panel (security-auditor) and a tie-breaker
(architect-reviewer); the owner approved the consolidated design.

## Facts (Tornado 6.5.10)
- `WebService.__init__` (`cms/io/web_service.py`) builds
  `HTTPServer(self.application)` with no options; size/timeout options can be
  passed through the existing `parameters.pop(...)` pattern.
- `body_timeout` is a total deadline for reading the body;
  `idle_connection_timeout` is the header/keep-alive wait (default 3600 s).
- A body over `max_body_size` makes Tornado write a bare `HTTP/1.1 400` and
  close: it never reaches `write_error` (no CMS page possible).
- Tornado's "Invalid body" is `raise HTTPError(400, ...) from HTTPInputError`
  inside `_execute`, before XSRF and `prepare()`.
- `resolve_remote_ip` runs only in `CommonRequestHandler.prepare()`, so the
  400 WARNING, XSRF 403s and the access log of early failures show the
  proxy's IP.

## Non-goals
Config keys or env vars for the limits; per-handler or per-content-type limits
(`stream_request_body`); `decompress_request`; a custom 413 page; matching on
Tornado's error text; a username in the error log; AWS-specific error pages; a
new IP header; `xheaders`; `idle_connection_timeout` (behind a proxy it governs
idle proxy-to-CMS keep-alives; below Caddy's upstream idle time it invites 502s
on POSTs).

## #65 — limits (constants, not config)
- Pass `max_body_size`, `max_buffer_size` and `body_timeout` from `parameters`
  to `HTTPServer` (Tornado's defaults when absent). `max_body_size` must not
  exceed `max_buffer_size` (Tornado buffers the whole body): set both.
- **CWS**: `MAX_BODY_SIZE = 32 MiB`, `BODY_TIMEOUT = 300 s` (largest legitimate
  body: a user test, at most 5 MB of input plus sources; OutputOnly ~100 KB ×
  outputs). Tornado is only a backstop; Caddy's `max_size` stays the precise
  limit and must stay below 32 MiB (a Tornado reject is a blank 400). Constants
  next to the existing `MAX_*` in the CWS server file.
- **AWS**: `MAX_BODY_SIZE = 100 MiB` written explicitly (tie-breaker: nobody
  knows the largest testcase zip uploaded via AWS, and upstream's nginx sample
  gives /admin/ 100M "for testcases"), `BODY_TIMEOUT = 600 s` (100 MiB in 600 s
  needs ~1.4 Mbit/s from the admin if Caddy streams the body).
- Slow links: with `request_buffers` (the arrival-time setup) Caddy absorbs the
  slow upload; without it 300 s still covers 3 MB at 256 kbit/s. A timeout
  closes the connection silently (looks like a network drop): document it.
- Docs only, for the urlencoded amplification: cap the non-upload CWS paths at
  64-256 KB in Caddy with a negated matcher (the smaller cap wins); mention
  Caddy's `servers { timeouts { read_header read_body } }` as an unverified
  suggestion. Update the "100 MiB" sentences in `docs/docker-deployment.md`
  ("Request body limits"), `docs/importing-users.md` (~38), `docs/contest-day.md`
  (~546-547) and the `shortcut:` comment in
  `cms/server/admin/handlers/contestuser.py` (~438) to the new numbers.

## #66 — message and real IP
- CWS `BaseHandler.write_error`: recognise
  `status_code == 400 and isinstance(exc, HTTPError) and
  isinstance(exc.__cause__, HTTPInputError)` (exc from `kwargs["exc_info"][1]`);
  pass `invalid_body=True` to `error.html`, where an `{% if %}` replaces the
  tamper/disqualification paragraph and the generic one with a new
  `{% trans %}` block (in the template, not in Python).
  EN msgid: "The server could not read the data of your request. It may have
  been damaged on its way, or it may contain too many files. Please try again;
  if you were sending output files one by one, send a single zip file instead.
  If the problem continues, notify the contest administrators."
  ES (formal like the existing strings): "El servidor no pudo leer los datos de
  su solicitud. Pueden haberse dañado en el camino o incluir demasiados
  archivos. Inténtelo de nuevo; si estaba enviando archivos de salida uno por
  uno, envíe un único archivo zip. Si el problema continúa, avise a los
  administradores del concurso." Add to `cms/locale/cms.pot` and
  `cms/locale/es/LC_MESSAGES/cms.po` (check whether an `es_CL` catalog exists
  and follow its policy). Then simplify the contest-day row (~546) that
  mentions disqualification.
- Real IP: move the `resolve_remote_ip(...)` block from `prepare()` to
  `CommonRequestHandler.__init__` (`self.service` is available there; keep the
  rightmost-N semantics). Fixes the Tornado WARNING, XSRF failures and the
  access log for CWS and AWS. Check that tests building a `CommonRequestHandler`
  with a bare MagicMock service still pass (it needs an int
  `num_proxies_used`). The INFO "Malformed HTTP message from …" for oversize
  bodies keeps the proxy IP: leave it.

## Tests
1. `web_service_test`: an oversize Content-Length → 400 and the handler does
   not run; headers then silence with a tiny `body_timeout` → connection
   closed.
2. CWS and AWS `server_test`: `service._http_server.conn_params.max_body_size`
   and `.body_timeout` equal the constants (and `max_buffer_size` ≥ body size).
3. CWS `body_limits_test`: `multipart_body(1000)` → 400 with the new text and
   without "tamper"; an XSRF 403 keeps the generic page;
   `assertLogs("tornado.general")` with `X-Forwarded-For` and
   `num_proxies_used=1` shows the resolved IP, not 127.0.0.1.
4. `util_test` (or the closest): an XSRF 403 logs the resolved IP.

## Out of scope for this branch
Other lanes edit `docs/contest-day.md` (other rows) and
`cms/server/admin/handlers/base.py` (#63, `try_commit`/`write_error` of AWS):
keep your edits there local.

## Tasks
1. #65 limits + tests. 2. #66 real IP + tests. 3. #66 message + catalogs +
tests. 4. Docs + docs check.
