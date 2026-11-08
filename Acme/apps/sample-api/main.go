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

// version is set at build time: go build -ldflags "-X main.version=<v>". CI passes sha-<first 7 hex of the commit>.
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

// parseFaultInjection accepts only "enabled" (on) or unset (off) and rejects anything else,
// so a typo fails startup instead of silently leaving /fault off, or on (R7).
func parseFaultInjection(raw string) (bool, error) {
	switch raw {
	case "":
		return false, nil
	case "enabled":
		return true, nil
	}
	return false, fmt.Errorf("invalid FAULT_INJECTION %q: must be \"enabled\", or unset to turn it off", raw)
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

// faultHandler always fails, so the error-rate alert can be proven with real 500s.
func faultHandler(w http.ResponseWriter, r *http.Request) {
	http.Error(w, "fault injected", http.StatusInternalServerError)
}

// newMux holds all route wiring, so tests exercise the real paths rather than calling handlers directly.
// /fault exists only when faults is on, which only dev's overlay sets (R7).
func newMux(version string, logger *slog.Logger, faults bool) *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", healthHandler(version, logger))
	if faults {
		mux.HandleFunc("GET /fault", faultHandler)
	}
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

// newServers builds the app server (every request counted) and the metrics server (only /metrics),
// sharing one registry. /metrics stays off the app's port, so the Ingress can never expose it.
func newServers(version string, logger *slog.Logger, faults bool) (appSrv, metricsSrv *http.Server) {
	m := newMetrics()
	metricsMux := http.NewServeMux()
	metricsMux.Handle("GET /metrics", m.handler())
	return newServer(m.instrument(newMux(version, logger, faults))), newServer(metricsMux)
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

// listening pairs a server with the listener it serves on.
type listening struct {
	srv *http.Server
	ln  net.Listener
}

// serveAll runs each server with serve, so a cancelled ctx (SIGTERM) drains them all, as does any
// one server stopping on its own; it returns only once every one has stopped, with their errors joined.
func serveAll(ctx context.Context, logger *slog.Logger, timeout time.Duration, servers ...listening) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	errs := make(chan error, len(servers))
	for _, s := range servers {
		go func() {
			errs <- serve(ctx, s.srv, s.ln, logger, timeout)
			cancel() // one server stopping, for any reason, drains the rest
		}()
	}
	var all []error
	for range servers {
		all = append(all, <-errs)
	}
	return errors.Join(all...)
}

func main() {
	level, err := parseLogLevel(getEnvOrDefault("LOG_LEVEL", "info"))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	faults, err := parseFaultInjection(os.Getenv("FAULT_INJECTION"))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level}))
	slog.SetDefault(logger)

	// Emit at no lower than the configured level so the resolved level is always visible.
	logger.Log(context.Background(), max(slog.LevelInfo, level), "starting",
		"version", version, "log_level", level.String(), "fault_injection", faults)

	if err := run(logger, faults); err != nil {
		logger.Error("server exited", "err", err)
		os.Exit(1)
	}
	logger.Info("stopped")
}

// run is split out of main so its deferred stop() runs before main calls os.Exit.
func run(logger *slog.Logger, faults bool) error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	ln, err := net.Listen("tcp", ":8080")
	if err != nil {
		return err
	}
	return serve(ctx, newServer(newMux(version, logger, faults)), ln, logger, shutdownTimeout)
}
