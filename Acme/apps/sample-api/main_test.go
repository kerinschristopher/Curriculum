package main

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"regexp"
	"slices"
	"strings"
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

// Only "enabled" turns /fault on and only unset leaves it off; anything else fails startup,
// so a typo can't silently leave it off (or on).
func TestParseFaultInjection(t *testing.T) {
	for raw, want := range map[string]bool{"": false, "enabled": true} {
		got, err := parseFaultInjection(raw)
		if err != nil || got != want {
			t.Errorf("parseFaultInjection(%q) = %v, %v; want %v, nil", raw, got, err, want)
		}
	}

	for _, raw := range []string{"Enabled", "true", "1", "on", "disabled", " enabled"} {
		if _, err := parseFaultInjection(raw); err == nil {
			t.Errorf("parseFaultInjection(%q) error = nil, want an error", raw)
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

// scrape returns what Prometheus would read from m's metrics handler.
func scrape(m *metrics) string {
	rec := httptest.NewRecorder()
	m.handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	return rec.Body.String()
}

// instrumentedApp serves the real mux through a fresh metrics middleware until the test ends.
func instrumentedApp(t *testing.T) (*metrics, *httptest.Server) {
	t.Helper()
	m := newMetrics()
	app := httptest.NewServer(m.instrument(newMux("1.2.3", discard)))
	t.Cleanup(app.Close)
	return m, app
}

// The counter's labels come from the mux's pattern, never the raw path, so a client can't add series (R8).
func TestRequestsAreCounted(t *testing.T) {
	m, app := instrumentedApp(t)

	for _, path := range []string{"/health", "/nope"} {
		resp, err := http.Get(app.URL + path)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		_ = resp.Body.Close()
	}

	body := scrape(m)

	for _, want := range []string{
		"# TYPE http_requests_total counter",
		`http_requests_total{code="200",method="GET",route="/health"} 1`,
		`http_requests_total{code="404",method="GET",route="unmatched"} 1`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("metrics output lacks %q\n--- output ---\n%s", want, body)
		}
	}
}

// The method comes from the client too, so a made-up one must not add series (R8).
func TestUnknownMethodsAreCountedAsOther(t *testing.T) {
	m, app := instrumentedApp(t)

	for _, method := range []string{"FOO", http.MethodPost} {
		req, err := http.NewRequest(method, app.URL+"/health", nil)
		if err != nil {
			t.Fatal(err)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("%s /health: %v", method, err)
		}
		_ = resp.Body.Close()
	}

	body := scrape(m)

	for _, want := range []string{
		`http_requests_total{code="405",method="other",route="unmatched"} 1`,
		`http_requests_total{code="405",method="POST",route="unmatched"} 1`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("metrics output lacks %q\n--- output ---\n%s", want, body)
		}
	}
	if strings.Contains(body, `method="FOO"`) {
		t.Errorf("a client-chosen method became a label value\n--- output ---\n%s", body)
	}
}

// The histogram carries our edges and labels; client_golang does the bucket maths, so the test doesn't redo it.
func TestRequestLatencyIsObserved(t *testing.T) {
	m, app := instrumentedApp(t)

	resp, err := http.Get(app.URL + "/health")
	if err != nil {
		t.Fatalf("GET /health: %v", err)
	}
	_ = resp.Body.Close()

	body := scrape(m)

	for _, want := range []string{
		"# TYPE http_request_duration_seconds histogram",
		`http_request_duration_seconds_count{code="200",method="GET",route="/health"} 1`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("metrics output lacks %q\n--- output ---\n%s", want, body)
		}
	}

	// Edges only, not per-bucket counts: those depend on how fast the request ran.
	edge := regexp.MustCompile(`(?m)^http_request_duration_seconds_bucket\{code="200",method="GET",route="/health",le="([^"]+)"\}`)
	var got []string
	for _, match := range edge.FindAllStringSubmatch(body, -1) {
		got = append(got, match[1])
	}
	want := []string{"0.005", "0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1", "2.5", "+Inf"}
	if !slices.Equal(got, want) {
		t.Errorf("bucket edges = %v, want %v\n--- output ---\n%s", got, want, body)
	}
}
