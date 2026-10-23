# Builder pinned by digest so a re-pushed tag can't change what we compile with; the tag is
# kept for readability. Bump both together, and keep the Go release in step with the
# sample-api-ci.yml test matrix.
#
# Single architecture (linux/amd64) on purpose. Multi-arch images (amd64 + arm64 under one
# tag via buildx, $BUILDPLATFORM/$TARGETARCH and an image index) are deferred to Module 7,
# where buildx is the topic, to keep the Module 6 change small enough to review. They also
# need the CI scan-then-push hand-off reworked (docs/design/module-6-ci-cd.md).
FROM golang:1.27.3-alpine@sha256:d530cf60723abf1ffae0cd16bb546b379ec0550d2f3815e5b16ed69dbc00fba4 AS builder
WORKDIR /src
COPY go.mod ./
COPY *.go ./
# Set by BuildKit; declared here so the stage can read it (the cache mount's id).
ARG TARGETARCH
# CI passes sha-<short>; a local build without --build-arg reports "dev".
ARG VERSION=dev
RUN --mount=type=cache,id=go-build-${TARGETARCH},target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags "-s -w -X main.version=${VERSION}" -o /server .

FROM scratch
COPY --from=builder /server /server
EXPOSE 8080
USER 65532:65532
ENTRYPOINT ["/server"]
