# DockLens — X-ray your Dockerfile before it ships

Most Dockerfiles are written once, copied from a tutorial, and never
revisited — and the same preventable mistakes keep showing up in
production: images running as root, secrets baked into ENV layers
(where they stay forever, even if a later layer "deletes" them),
`:latest` base images that change under you, `curl | sh` pipes,
bloated layers from careless `apt-get` use, and `COPY . .` shipping
your `.env` into the image. You only find out when the incident
happens.

DockLens is a zero-dependency Python CLI that audits a Dockerfile
*before* it ships: it parses every instruction (multi-stage aware,
line continuations, JSON-form `CMD`/`ENTRYPOINT`), runs 25 rules
across security, efficiency, reliability, and maintainability, and
scores the file 0–100 with a letter grade.

## What it checks

| Area | Examples |
|---|---|
| Security | Secrets in `ENV` / secret `ARG`s used in `RUN`, `curl \| sh`, running as root, `ADD` fetching URLs, `COPY` of `.env` files, `EXPOSE 22`, `chmod 777`, `sudo` in build steps |
| Efficiency | Unpinned / `:latest` base images, `apt-get` without `--no-install-recommends` or layer cleanup, split update/install layers, `COPY . .` before dependency install (cache busting), `apk`/`pip`/`npm` hygiene |
| Reliability | Shell-form `CMD`/`ENTRYPOINT` swallowing signals, missing `HEALTHCHECK`, missing `WORKDIR`, multi-stage builds that never `COPY --from=` the artifact |
| Maintainability | Deprecated `MAINTAINER`, digest-pinning hints |

Each finding carries a rule id (DL01–DL25), severity
(critical / warning / info), the exact line, why it matters, and a
concrete fix. Score: critical −15, warning −7, info −2, floored at
0. Verdict: `FAIL` if any critical finding, `NEEDS WORK` if any
warning, otherwise `CLEAN`.

## Install & run

No dependencies — Python 3.8+ standard library only.

```bash
python -m docklens path/to/Dockerfile
python -m docklens --format json path/to/Dockerfile
python -m docklens --format html --output report.html path/to/Dockerfile
python -m docklens --context ./my-app path/to/Dockerfile   # also checks for .dockerignore
python -m docklens --fail-on warning path/to/Dockerfile    # CI gate: exit 1 on warning+
```

Exit codes: `0` pass, `1` `--fail-on` threshold tripped, `2` usage
or file error.

## Example output

Bad example (the shipped `examples/bad/Dockerfile`):

```
$ python -m docklens examples/bad/Dockerfile
DockLens report: examples/bad/Dockerfile
Score 0/100 (grade F)  Verdict: FAIL  critical 2  warning 11  info 4
[WARNING] DL01 (line 3) Base image pinned to :latest
    FROM node:latest is not reproducible: the next pull may differ.
    Fix: Pin a specific version, or pin by digest with @sha256:....
[WARNING] DL04 (line 3) Final stage runs as root
    No USER is set in the final stage, so the container process runs as UID 0.
    Fix: Add a non-root user, e.g. RUN adduser -S app && USER app (prefer a numeric UID for Kubernetes runAsNonRoot).
[INFO] DL22 (line 3) No HEALTHCHECK defined
    Orchestrators cannot tell a wedged container from a healthy one without a health check.
    Fix: Add a HEALTHCHECK that hits your app's health endpoint and exits 1 when it fails.
[INFO] DL25 (line 3) No WORKDIR set in the final stage
    Relative COPY destinations and CMD paths resolve against /, which is easy to get wrong.
    Fix: Set WORKDIR /app early in the final stage.
[INFO] DL23 (line 4) MAINTAINER is deprecated
    MAINTAINER was deprecated in favour of labels.
    Fix: Use LABEL maintainer="name <email>" instead.
[CRITICAL] DL05 (line 5) Secret baked into an ENV layer
    ENV API_KEY=... stores the value in the image layer forever, even if a later layer deletes it.
    Fix: Inject secrets at runtime (orchestrator secrets, secret manager) or use a BuildKit secret mount for build-time needs; never ENV them.
[CRITICAL] DL07 (line 7) Download piped straight into a shell
    curl|sh runs unverified code from the network.
    Fix: Download the file, verify its checksum/signature, then execute it.
[WARNING] DL09 (line 8) apt-get update cached in its own layer
    A lone apt-get update layer goes stale while later installs reuse the cached package lists.
    Fix: Combine update and install in one RUN: apt-get update && apt-get install -y ...
[WARNING] DL10 (line 9) apt-get install pulls recommended packages
    Recommended packages bloat the image with libraries you never asked for.
    Fix: Add --no-install-recommends to apt-get install.
[WARNING] DL11 (line 9) apt lists left in the image layer
    Package lists downloaded by apt-get update stay in the layer unless removed in the same RUN.
    Fix: End the RUN with: rm -rf /var/lib/apt/lists/*
[WARNING] DL08 (line 10) ADD fetches a remote URL
    ADD https://example.com/tool.tar.gz downloads at build time with no checksum verification.
    Fix: Use curl/wget with a checksum check, then COPY.
[WARNING] DL17 (line 11) COPY . . with no .dockerignore
    Without a .dockerignore, COPY . . can ship .env files, .git, and local secrets into the image.
    Fix: Add a .dockerignore excluding .env, .git, node_modules, and other local-only files.
[WARNING] DL18 (line 11) COPY . . before dependency install busts the cache
    Copying the whole context before installing dependencies invalidates the install layer on every code change.
    Fix: COPY only lockfiles first, install, then COPY the rest of the source.
[WARNING] DL06 (line 12) Secret ARG value leaks into a layer
    ARG NPM_TOKEN is used in a RUN step, so its value is recorded in the image history (docker history).
    Fix: Use a BuildKit secret mount instead: RUN --mount=type=secret,id=npm_token ...
[INFO] DL14 (line 12) npm install instead of npm ci
    npm install can resolve different versions than the lockfile; npm ci is reproducible.
    Fix: Use npm ci when a lockfile exists.
[WARNING] DL20 (line 13) SSH port 22 exposed
    Containers almost never need SSH; exposing port 22 widens the attack surface.
    Fix: Remove EXPOSE 22; use docker exec for debugging.
[WARNING] DL21 (line 14) Shell-form CMD swallows signals
    CMD npm start runs via /bin/sh -c, so SIGTERM may never reach your app and shutdowns hang.
    Fix: Use exec form: CMD ["executable", "arg1"]
```

Clean example (the shipped `examples/good/Dockerfile`):

```
$ python -m docklens examples/good/Dockerfile
DockLens report: examples/good/Dockerfile
Score 98/100 (grade A)  Verdict: CLEAN  critical 0  warning 0  info 1
[INFO] DL02 (line 3) Base image not pinned by digest
    FROM node:20.11.0-alpine3.19 is version-pinned, but tags are mutable.
    Fix: For fully reproducible builds, pin by digest: FROM image:tag@sha256:....
```

The multi-stage example (`examples/multistage/Dockerfile`) scores
94/100, grade A, `CLEAN` — its only notable finding is DL24, the
final stage never copying the builder's artifact.

## Tests

```bash
python -m unittest discover -s tests -v   # 34 tests
```

## Live demo

`docs/` ships a landing page plus a real sample report generated by
DockLens from `examples/bad/Dockerfile` — open `docs/index.html`
locally, or serve it at `https://judd69.github.io/docklens/` once
Pages is enabled (Settings → Pages → branch main, folder /docs).

## Rules reference

DL01 unpinned/`:latest` base · DL02 no digest pin · DL03 no FROM ·
DL04 runs as root · DL05 secret in ENV · DL06 secret ARG in RUN ·
DL07 `curl|sh` · DL08 ADD misuse · DL09 split apt update/install ·
DL10 apt without `--no-install-recommends` · DL11 apt lists left in
layer · DL12 apk without `--no-cache` · DL13 pip without
`--no-cache-dir` · DL14 `npm install` vs `npm ci` · DL15 sudo in
RUN · DL16 `chmod 777` · DL17 `COPY . .` without `.dockerignore` ·
DL18 `COPY . .` before install · DL19 `.env` copied into image ·
DL20 `EXPOSE 22` · DL21 shell-form CMD/ENTRYPOINT · DL22 no
HEALTHCHECK · DL23 deprecated MAINTAINER · DL24 multi-stage without
`COPY --from=` · DL25 no WORKDIR.

## License

MIT — original work, written for the daily-build series.
