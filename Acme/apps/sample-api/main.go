package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

// version is set at build time: go build -ldflags "-X main.version=<v>". CI passes sha-<short>.
var version = "dev"

// shutdownTimeout must stay below the pod's terminationGracePeriodSeconds (Kubernetes default 30s),
// otherwise the kubelet SIGKILLs the process while it is still draining requests.
const shutdownTimeout = 20 * time.Second

func getEnvOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// parseLogLevel accepts debug|info|warn|error in any case and rejects anything else,
// so a typo fails startup instead of silently running at the wrong level.
func parseLogLevel(raw string) (slog.Level, error) {
	var level slog.Level
	if err := level.UnmarshalText([]byte(strings.TrimSpace(raw))); err != nil {
		return 0, fmt.Errorf("invalid LOG_LEVEL %q: must be one of debug, info, warn, error", raw)
	}
	return level, nil
}

type healthResponse struct {
	Status  string `json:"status"`
	Version string `json:"version"`
}

func healthHandler(version string, logger *slog.Logger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		logger.Debug("request", "method", r.Method, "path", r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(healthResponse{Status: "ok", Version: version}); err != nil {
			logger.Error("writing health response", "err", err)
		}
	}
}

// newMux holds all route wiring, so tests exercise the real paths rather than calling handlers directly.
func newMux(version string, logger *slog.Logger) *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", healthHandler(version, logger))
	return mux
}

// newServer sets every timeout so slow or idle clients can't hold connections open indefinitely.
func newServer(h http.Handler) *http.Server {
	return &http.Server{
		Handler:           h,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
}

// serve runs srv on ln until ctx is cancelled, then stops accepting connections and waits up to
// timeout for in-flight requests to finish. Kubernetes sends SIGTERM when it stops a pod; readiness
// takes the pod out of the Service, and this drain lets requests already in flight complete.
func serve(ctx context.Context, srv *http.Server, ln net.Listener, logger *slog.Logger, timeout time.Duration) error {
	errCh := make(chan error, 1)
	go func() { errCh <- srv.Serve(ln) }()

	select {
	case err := <-errCh:
		return err // the server stopped on its own, so it never started cleanly
	case <-ctx.Done():
	}

	logger.Info("shutting down", "timeout", timeout.String())
	shutdownCtx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		return fmt.Errorf("draining requests: %w", err)
	}
	if err := <-errCh; !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

func main() {
	level, err := parseLogLevel(getEnvOrDefault("LOG_LEVEL", "info"))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level}))
	slog.SetDefault(logger)

	// Emit at no lower than the configured level so the resolved level is always visible.
	logger.Log(context.Background(), max(slog.LevelInfo, level), "starting",
		"version", version, "log_level", level.String())

	if err := run(logger); err != nil {
		logger.Error("server exited", "err", err)
		os.Exit(1)
	}
	logger.Info("stopped")
}

// run is split out of main so its deferred stop() runs before main calls os.Exit.
func run(logger *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	ln, err := net.Listen("tcp", ":8080")
	if err != nil {
		return err
	}
	return serve(ctx, newServer(newMux(version, logger)), ln, logger, shutdownTimeout)
}
