package main

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

var discard = slog.New(slog.DiscardHandler)

// Written before parseLogLevel existed (PR #5 review: test-first, not a backfill).
func TestParseLogLevel(t *testing.T) {
	valid := []struct {
		raw  string
		want slog.Level
	}{
		{"debug", slog.LevelDebug},
		{"DEBUG", slog.LevelDebug},
		{"Info", slog.LevelInfo},
		{" warn ", slog.LevelWarn},
		{"error", slog.LevelError},
	}
	for _, tc := range valid {
		got, err := parseLogLevel(tc.raw)
		if err != nil {
			t.Errorf("parseLogLevel(%q) error = %v, want nil", tc.raw, err)
			continue
		}
		if got != tc.want {
			t.Errorf("parseLogLevel(%q) = %v, want %v", tc.raw, got, tc.want)
		}
	}

	// An operator typo must fail startup, not silently run at the default level.
	for _, raw := range []string{"", "verbose", "trace", "debg"} {
		if _, err := parseLogLevel(raw); err == nil {
			t.Errorf("parseLogLevel(%q) error = nil, want an error", raw)
		}
	}
}

// Salvaged from depmod6; the handler now takes a logger instead of a raw log-level string.
func TestHealthHandler(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()

	healthHandler("1.2.3", discard)(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusOK)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("Content-Type = %q, want %q", ct, "application/json")
	}

	var body healthResponse
	if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
		t.Fatalf("response is not valid JSON: %v", err)
	}
	if body.Status != "ok" {
		t.Errorf("status field = %q, want %q", body.Status, "ok")
	}
	if body.Version != "1.2.3" {
		t.Errorf("version field = %q, want %q", body.Version, "1.2.3")
	}
}

// Goes through the real mux, so a wrong route path fails here (PR #6 review).
func TestRoutes(t *testing.T) {
	srv := httptest.NewServer(newMux("1.2.3", discard))
	defer srv.Close()

	cases := []struct {
		method, path string
		want         int
	}{
		{http.MethodGet, "/health", http.StatusOK},
		{http.MethodGet, "/nope", http.StatusNotFound},
		{http.MethodGet, "/health/extra", http.StatusNotFound},
		{http.MethodPost, "/health", http.StatusMethodNotAllowed},
	}
	for _, tc := range cases {
		req, err := http.NewRequest(tc.method, srv.URL+tc.path, nil)
		if err != nil {
			t.Fatal(err)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("%s %s: %v", tc.method, tc.path, err)
		}
		_ = resp.Body.Close()
		if resp.StatusCode != tc.want {
			t.Errorf("%s %s = %d, want %d", tc.method, tc.path, resp.StatusCode, tc.want)
		}
	}
}

// A request already in flight when shutdown starts must still complete, and serve must wait for it.
func TestServeDrainsInFlightRequests(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	started := make(chan struct{})
	release := make(chan struct{})
	slow := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(started)
		<-release
		_, _ = io.WriteString(w, "done")
	})

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	served := make(chan error, 1)
	go func() { served <- serve(ctx, newServer(slow), ln, discard, 5*time.Second) }()

	type result struct {
		status int
		err    error
	}
	got := make(chan result, 1)
	go func() {
		resp, err := http.Get("http://" + ln.Addr().String())
		if err != nil {
			got <- result{err: err}
			return
		}
		_ = resp.Body.Close()
		got <- result{status: resp.StatusCode}
	}()

	<-started
	cancel() // what SIGTERM does in main

	select {
	case err := <-served:
		t.Fatalf("serve returned (%v) while a request was still in flight", err)
	case <-time.After(200 * time.Millisecond):
	}

	close(release)
	if r := <-got; r.err != nil || r.status != http.StatusOK {
		t.Fatalf("in-flight request: status %d, err %v; want 200, nil", r.status, r.err)
	}
	select {
	case err := <-served:
		if err != nil {
			t.Fatalf("serve() = %v, want nil after a clean drain", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("serve did not return after the in-flight request finished")
	}
}
