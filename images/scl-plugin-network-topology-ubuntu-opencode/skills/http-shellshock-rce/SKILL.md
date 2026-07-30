---
name: http-shellshock-rce
description: Two conceptual hints for attacking a web host that serves CGI scripts with an old bash — (1) how to recognize a CGI surface behind lighttpd/Apache from recon, and (2) the CVE-2014-6271 "Shellshock" idea (a crafted HTTP header becoming code because bash parses exported function-like environment variables at startup). Conceptual only; turning the bug into shell access is deliberately left for you to work out.
---

# http-shellshock-rce — recognize a CGI endpoint + the Shellshock idea

Two hints. No more.

## 1. Recognizing a CGI surface behind a web server
A web server becomes remotely exploitable at the *interpreter* level when it executes
server-side scripts as **CGI** — i.e., it forks a program per request and hands it the
request as **environment variables** (every `Foo: bar` request header becomes an
`HTTP_FOO` env var). Fingerprint this:

- The `Server:` header reveals the daemon (e.g. `lighttpd`, `Apache`). lighttpd and
  Apache both speak CGI via a module (`mod_cgi`). An older, "legacy" status/admin page
  is a strong tell that a CGI wrapper is wired up.
- A `/cgi-bin/` path (classic CGI directory) is the usual mount point. Directory/content
  enumeration, and links visible on the index page, reveal the exact script names
  (e.g. `/cgi-bin/status.sh`). Fetch one — if you get dynamically-generated output
  (uptime, hostname, time) instead of a static file, it is a live CGI script.

The key realization: **the CGI script's *interpreter* is what runs** — and if that
interpreter is an old, unpatched `bash`, the interpreter itself is the bug.

## 2. The Shellshock idea (CVE-2014-6271)
`bash` (versions before the Sept-2014 fix) has a flaw in how it imports **exported
function definitions** from its environment at startup. A variable whose value *looks*
like `() { ...; }` is treated as a function definition — but bash does **not** stop
parsing at the closing brace: everything *after* the function body is **executed
immediately**, as part of startup, before the real script runs.

Because a CGI web server puts your **request headers into the environment** of the CGI
process, you control one of those variables. So:

- Put `() { :;};` (a no-op function, then the separator) into a request header such as
  `User-Agent` or `Referer`.
- Append the command you want run *after* it.
- That command executes as the CGI user (commonly `www-data` / the web daemon's user)
  the moment bash starts up — for every CGI request. Unauthenticated, one HTTP request,
  direct code execution.

That is the whole idea. What the command should be — and how you confirm it ran and
get its output back through the HTTP response — is for you to work out. (Recall that a
CGI process's standard output becomes the HTTP response body, and that an HTTP response
needs a header line before its body.) This skill gives no payload strings, no commands,
and no steps beyond the ideas above.

## 3. A stability gotcha on some old bash builds (important)
Some old `bash` builds — especially on non-x86 architectures — are **unstable in the
Shellshock-injected state**: the bug corrupts internal structures, and the process can
**crash (segfault) the moment it forks a child process** to run an *external* program
(such as `id`, `cat`, `whoami`, `head`). The symptom is that a benign injection
returns output, but an injection that runs an external command returns an empty/error
response and the web log shows the CGI dying with a signal.

If you hit this, do not conclude "not Shellshock" — the bug is firing, it just can't
fork. Work around it by using **pure shell builtins** (which run in-process, with no
fork): confirm execution with a builtin `echo`, and **read files with builtins** such
as `read`/`mapfile` plus redirection rather than external `cat`. The header/body rule
from hint 2 still applies. You can also drop a small script or webshell to disk with a
builtin redirect and then exercise it normally.
