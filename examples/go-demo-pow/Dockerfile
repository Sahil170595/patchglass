# Bakes the synthetic Go module at its base (buggy) commit, ready for grading via `go test`.
# Stdlib-only, so `go test` needs no network at grade time (GOTOOLCHAIN=local avoids toolchain fetches).
FROM golang:1.22
ENV GOTOOLCHAIN=local GOFLAGS=-mod=mod CGO_ENABLED=0
WORKDIR /app
COPY repo/ /app/
RUN go build ./... >/dev/null 2>&1 || true
RUN git init -q \
 && git config user.email tb@example.com \
 && git config user.name taskbundle \
 && git add -A \
 && GIT_AUTHOR_DATE="2020-01-01T00:00:00Z" GIT_COMMITTER_DATE="2020-01-01T00:00:00Z" \
    git commit -qm base
