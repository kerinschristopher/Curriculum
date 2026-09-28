FROM golang:1.27-alpine AS builder
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -o /server .

FROM scratch
COPY --from=builder /server /server
EXPOSE 8080
# Run as an unprivileged user; scratch has no /etc/passwd, so use a numeric UID:GID.
USER 65534:65534
ENTRYPOINT ["/server"]