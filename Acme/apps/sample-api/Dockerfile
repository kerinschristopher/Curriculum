FROM golang:1.23-alpine AS builder
WORKDIR /src
COPY go.mod ./
COPY main.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -o /server .

FROM scratch
COPY --from=builder /server /server
EXPOSE 8080
USER 65532:65532
ENTRYPOINT ["/server"]