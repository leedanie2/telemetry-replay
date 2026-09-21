# Docker and continuous integration

The Dockerfile starts with Python 3.11 slim, installs pinned dependencies before
copying source files, and runs the CLI under UID 10001. Code changes can reuse the
dependency layer when cache is available. ENTRYPOINT selects the CLI; CMD defaults
to demo, and a Compose command such as `app report --device A` replaces that default.

Compose starts PostgreSQL 18, waits for pg_isready, and runs the app on the internal
network. No database port is published on the host. PostgreSQL 18's persistent
volume is mounted at /var/lib/postgresql; data resides in its version-specific
subdirectory. A health check establishes readiness, while the actual app/test
connections verify credentials and SQL behavior.

The literal Compose password is an intentional disposable development/CI value,
not a secret or deployment credential. The image-created database role has schema
administration privileges for isolated tests. Use a separate authentication and
least-privilege plan before any deployment.

GitHub Actions runs on pushes, pull requests, and manual dispatch. It validates
Compose, builds the image, runs unit tests, starts PostgreSQL, runs all 25 tests,
executes the isolated demo, and smoke-tests persistent initialization and replay.
It also checks filtered/unknown-device CLI behavior and refuses a second schema
initialization. CI has read-only repository permissions and a 15-minute timeout.
Cleanup removes only its disposable containers and volume. No image publication
or deployment is performed.

Checkout is pinned to an action commit. Python/PostgreSQL image tags constrain
versions but remain mutable; this is operational reproducibility, not a guarantee
of bit-identical images over time. See the public Actions log for the current
container verification, rather than inferring success from the workflow file alone.
