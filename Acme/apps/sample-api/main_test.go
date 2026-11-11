package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"regexp"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

var discard = slog.New(slog.DiscardHandler)

// logLines is a test logger's output: each record arrives as one line on the channel.
type logLines chan string

func (l logLines) Write(p []byte) (int, error) { l <- string(p); return len(p), nil }

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
	srv := httptest.NewServer(newMux("1.2.3", discard, false))
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
		t.Run(tc.method+" "+tc.path, func(t *testing.T) {
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
		})
	}
}

// Requests already in flight on both servers when shutdown starts must still complete, and serveAll
// must wait for every server, not just the first to finish.
func TestServeDrainsInFlightRequests(t *testing.T) {
	type result struct {
		status int
		err    error
	}
	var started sync.WaitGroup
	var servers []listening
	var releases []chan struct{}
	for range 2 {
		ln, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		release := make(chan struct{})
		started.Add(1)
		slow := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			started.Done()
			<-release
			_, _ = io.WriteString(w, "done")
		})
		servers = append(servers, listening{srv: newServer(slow), ln: ln})
		releases = append(releases, release)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	served := make(chan error, 1)
	go func() { served <- serveAll(ctx, discard, 5*time.Second, servers[0], servers[1]) }()

	got := make(chan result, len(servers))
	for _, s := range servers {
		go func() {
			resp, err := http.Get("http://" + s.ln.Addr().String())
			if err != nil {
				got <- result{err: err}
				return
			}
			_ = resp.Body.Close()
			got <- result{status: resp.StatusCode}
		}()
	}

	started.Wait()
	cancel() // what SIGTERM does in main

	for i, release := range releases {
		select {
		case err := <-served:
			t.Fatalf("serveAll returned (%v) with %d request(s) still in flight", err, len(releases)-i)
		case <-time.After(200 * time.Millisecond):
		}
		close(release)
		if r := <-got; r.err != nil || r.status != http.StatusOK {
			t.Fatalf("in-flight request: status %d, err %v; want 200, nil", r.status, r.err)
		}
	}
	select {
	case err := <-served:
		if err != nil {
			t.Fatalf("serveAll() = %v, want nil after a clean drain", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("serveAll did not return after every in-flight request finished")
	}
}

// scrape returns what Prometheus would read from m's metrics handler.
func scrape(m *metrics) string {
	rec := httptest.NewRecorder()
	m.handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	return rec.Body.String()
}

// instrumentedApp serves the real mux through a fresh metrics middleware until the test ends.
func instrumentedApp(t *testing.T, faults bool) (*metrics, *httptest.Server) {
	t.Helper()
	m := newMetrics()
	app := httptest.NewServer(m.instrument(newMux("1.2.3", discard, faults)))
	t.Cleanup(app.Close)
	return m, app
}

// Requests are counted by the histogram's _count, labelled from the mux's pattern, never the raw path, so a
// client can't add series (R8). There is no separate counter: it would hold the same numbers twice.
func TestRequestsAreCounted(t *testing.T) {
	m, app := instrumentedApp(t, false)

	for _, path := range []string{"/health", "/nope"} {
		resp, err := http.Get(app.URL + path)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		_ = resp.Body.Close()
	}

	body := scrape(m)

	for _, want := range []string{
		`http_request_duration_seconds_count{code="200",method="GET",route="/health"} 1`,
		`http_request_duration_seconds_count{code="404",method="GET",route="unmatched"} 1`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("metrics output lacks %q\n--- output ---\n%s", want, body)
		}
	}
	if strings.Contains(body, "http_requests_total") {
		t.Errorf("metrics output still has http_requests_total, a copy of http_request_duration_seconds_count\n--- output ---\n%s", body)
	}
}

// The method comes from the client too, so a made-up one must not add series (R8).
func TestUnknownMethodsAreCountedAsOther(t *testing.T) {
	m, app := instrumentedApp(t, false)

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
		`http_request_duration_seconds_count{code="405",method="other",route="unmatched"} 1`,
		`http_request_duration_seconds_count{code="405",method="POST",route="unmatched"} 1`,
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
	m, app := instrumentedApp(t, false)

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

// A handler behind instrument() must reach the real writer's features (Flush, write deadlines) through
// http.ResponseController, which finds them by calling Unwrap on each wrapper.
func TestHandlersBehindInstrumentCanFlush(t *testing.T) {
	var flushErr error
	mux := http.NewServeMux()
	mux.HandleFunc("GET /stream", func(w http.ResponseWriter, r *http.Request) {
		flushErr = http.NewResponseController(w).Flush()
	})
	rec := httptest.NewRecorder()
	newMetrics().instrument(mux).ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/stream", nil))

	if flushErr != nil {
		t.Fatalf("Flush behind instrument() = %v, want nil", flushErr)
	}
	if !rec.Flushed {
		t.Error("Flush returned nil but the real writer was never flushed")
	}
}

// The code label must be the status the client got. Go sends only the first final status a handler sets, and
// a Write or a Flush before any WriteHeader has already sent 200, so a WriteHeader after either changes
// nothing. A 1xx such as 103 Early Hints is informational: the real status still follows it (E9, pre-review).
func TestStatusLabelIsTheOneSent(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /twice", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.WriteHeader(http.StatusOK)
	})
	mux.HandleFunc("GET /late", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("partial"))
		w.WriteHeader(http.StatusInternalServerError)
	})
	mux.HandleFunc("GET /hints", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusEarlyHints)
		w.WriteHeader(http.StatusOK)
	})
	mux.HandleFunc("GET /flush", func(w http.ResponseWriter, r *http.Request) {
		_ = http.NewResponseController(w).Flush()
		w.WriteHeader(http.StatusInternalServerError)
	})
	m := newMetrics()
	// A real server, not httptest.NewRecorder: the recorder takes a 1xx as the final status. The server logs
	// each ignored WriteHeader ("superfluous response.WriteHeader call"); here that is expected.
	app := httptest.NewServer(m.instrument(mux))
	t.Cleanup(app.Close)

	for _, tc := range []struct {
		path      string
		sent      int
		wantCount string
	}{
		{"/twice", http.StatusInternalServerError, `http_request_duration_seconds_count{code="500",method="GET",route="/twice"} 1`},
		{"/late", http.StatusOK, `http_request_duration_seconds_count{code="200",method="GET",route="/late"} 1`},
		{"/hints", http.StatusOK, `http_request_duration_seconds_count{code="200",method="GET",route="/hints"} 1`},
		{"/flush", http.StatusOK, `http_request_duration_seconds_count{code="200",method="GET",route="/flush"} 1`},
	} {
		t.Run(strings.TrimPrefix(tc.path, "/"), func(t *testing.T) {
			resp, err := http.Get(app.URL + tc.path)
			if err != nil {
				t.Fatalf("GET %s: %v", tc.path, err)
			}
			// To the end: /flush sends its headers early, and instrument() counts the request only after the
			// handler returns, which is before the body ends.
			_, _ = io.Copy(io.Discard, resp.Body)
			_ = resp.Body.Close()
			if resp.StatusCode != tc.sent {
				t.Fatalf("GET %s sent %d, want %d: the test's handler is wrong, not instrument()", tc.path, resp.StatusCode, tc.sent)
			}
			if body := scrape(m); !strings.Contains(body, tc.wantCount) {
				t.Errorf("metrics output lacks %q\n--- output ---\n%s", tc.wantCount, body)
			}
		})
	}
}

// /fault answers 500 only when switched on, and those 500s are counted like any route's: they are the alert's input.
// Switched off, it is just an unknown path.
func TestFaultRoute(t *testing.T) {
	for _, tc := range []struct {
		name      string
		faults    bool
		wantCode  int
		wantCount string
	}{
		{"on", true, http.StatusInternalServerError, `http_request_duration_seconds_count{code="500",method="GET",route="/fault"} 1`},
		{"off", false, http.StatusNotFound, `http_request_duration_seconds_count{code="404",method="GET",route="unmatched"} 1`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m, app := instrumentedApp(t, tc.faults)

			resp, err := http.Get(app.URL + "/fault")
			if err != nil {
				t.Fatalf("faults=%v: GET /fault: %v", tc.faults, err)
			}
			_ = resp.Body.Close()

			if resp.StatusCode != tc.wantCode {
				t.Errorf("faults=%v: GET /fault = %d, want %d", tc.faults, resp.StatusCode, tc.wantCode)
			}
			if body := scrape(m); !strings.Contains(body, tc.wantCount) {
				t.Errorf("faults=%v: metrics output lacks %q\n--- output ---\n%s", tc.faults, tc.wantCount, body)
			}
		})
	}
}

// The library's Go runtime and process collectors ride on the app's own registry (design §3).
func TestRuntimeMetricsAreExported(t *testing.T) {
	body := scrape(newMetrics())
	for _, want := range []string{
		"go_goroutines ",             // Go runtime collector
		"process_cpu_seconds_total ", // process collector
	} {
		if !strings.Contains(body, want) {
			t.Errorf("metrics output lacks %q\n--- output ---\n%s", want, body)
		}
	}
}

// /metrics is served only by the metrics server (:8081), so the Ingress (→ :8080) never exposes it,
// and requests to the app server are counted in what the metrics server shows.
func TestMetricsOnlyOnTheMetricsServer(t *testing.T) {
	appSrv, metricsSrv := newServers("1.2.3", discard, false)
	app := httptest.NewServer(appSrv.Handler)
	t.Cleanup(app.Close)
	mets := httptest.NewServer(metricsSrv.Handler)
	t.Cleanup(mets.Close)

	get := func(t *testing.T, base, path string) (int, string) {
		t.Helper()
		resp, err := http.Get(base + path)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		defer func() { _ = resp.Body.Close() }()
		body, err := io.ReadAll(resp.Body)
		if err != nil {
			t.Fatalf("reading %s: %v", path, err)
		}
		return resp.StatusCode, string(body)
	}

	for _, tc := range []struct {
		server, base, path string
		want               int
	}{
		{"app", app.URL, "/health", http.StatusOK},
		{"app", app.URL, "/metrics", http.StatusNotFound},
		{"metrics", mets.URL, "/health", http.StatusNotFound},
	} {
		t.Run(tc.server+" "+tc.path, func(t *testing.T) {
			if got, _ := get(t, tc.base, tc.path); got != tc.want {
				t.Errorf("%s server GET %s = %d, want %d", tc.server, tc.path, got, tc.want)
			}
		})
	}

	code, body := get(t, mets.URL, "/metrics")
	if code != http.StatusOK {
		t.Fatalf("metrics server GET /metrics = %d, want 200", code)
	}
	if want := `http_request_duration_seconds_count{code="200",method="GET",route="/health"} 1`; !strings.Contains(body, want) {
		t.Errorf("metrics output lacks %q\n--- output ---\n%s", want, body)
	}
}

// If the app server stops on its own there is nothing left to serve: serveAll must shut the metrics
// server down and return the app's error, so the process exits and Kubernetes restarts the pod.
func TestAppServerFailingStopsEverything(t *testing.T) {
	broken, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	_ = broken.Close() // its Serve fails at once, as a crashed server would
	metricsLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}

	served := make(chan error, 1)
	go func() {
		served <- serveAll(context.Background(), discard, 5*time.Second, // no SIGTERM
			listening{srv: newServer(http.NotFoundHandler()), ln: broken},
			listening{srv: newServer(http.NotFoundHandler()), ln: metricsLn})
	}()

	select {
	case err := <-served:
		if !errors.Is(err, net.ErrClosed) {
			t.Fatalf("serveAll() = %v, want the app server's error (net.ErrClosed)", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("serveAll kept waiting after the app server failed: the metrics server was never shut down")
	}
}

// The metrics server is telemetry: if it stops, the app must keep serving and the failure must be logged
// at Error. Prometheus already reports it (up == 0); stopping the app as well would turn that into an outage.
func TestMetricsServerFailingLeavesTheAppServing(t *testing.T) {
	appLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	broken, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	_ = broken.Close() // its Serve fails at once, as a crashed server would

	lines := make(logLines, 16)
	logger := slog.New(slog.NewJSONHandler(lines, nil))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	served := make(chan error, 1)
	go func() {
		served <- serveAll(ctx, logger, 5*time.Second,
			listening{srv: newServer(newMux("1.2.3", discard, false)), ln: appLn},
			listening{srv: newServer(http.NotFoundHandler()), ln: broken})
	}()

	// Wait for the failure to be logged; serveAll returning first means the app went down with it.
	for logged := false; !logged; {
		select {
		case line := <-lines:
			logged = strings.Contains(line, `"level":"ERROR"`) && strings.Contains(line, "metrics server stopped")
		case err := <-served:
			t.Fatalf("serveAll returned (%v) when only the metrics server failed: the app went down with it", err)
		case <-time.After(5 * time.Second):
			t.Fatal("the metrics server's failure was never logged at Error")
		}
	}

	resp, err := http.Get("http://" + appLn.Addr().String() + "/health")
	if err != nil {
		t.Fatalf("GET /health after the metrics server failed: %v", err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("GET /health after the metrics server failed = %d, want 200", resp.StatusCode)
	}

	http.DefaultClient.CloseIdleConnections() // else shutdown waits up to 5 s on the idle connection
	cancel()                                  // as SIGTERM would
	if err := <-served; err != nil {
		t.Errorf("serveAll() = %v after SIGTERM, want nil: the metrics failure was already logged", err)
	}
}

func TestServeOnPairsEachListenerWithItsServer(t *testing.T) {
	// run() opens :8080 for the Service and Ingress and :8081 for Prometheus. Paired the wrong way round,
	// /metrics would be public and the probes would get 404, and no other test would notice.
	appLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	metricsLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	served := make(chan error, 1)
	go func() { served <- serveOn(ctx, discard, false, appLn, metricsLn) }()

	for _, tc := range []struct {
		name string
		ln   net.Listener
		path string
		want int
	}{
		{"app", appLn, "/health", http.StatusOK},
		{"app", appLn, "/metrics", http.StatusNotFound},
		{"metrics", metricsLn, "/metrics", http.StatusOK},
		{"metrics", metricsLn, "/health", http.StatusNotFound},
	} {
		t.Run(tc.name+" "+tc.path, func(t *testing.T) {
			resp, err := http.Get("http://" + tc.ln.Addr().String() + tc.path)
			if err != nil {
				t.Fatalf("GET %s on the %s listener: %v", tc.path, tc.name, err)
			}
			_ = resp.Body.Close()
			if resp.StatusCode != tc.want {
				t.Errorf("GET %s on the %s listener = %d, want %d", tc.path, tc.name, resp.StatusCode, tc.want)
			}
		})
	}

	// The client keeps spare connections open; on shutdown the server waits up to 5 s for one that
	// hasn't sent a request, so close them first.
	http.DefaultClient.CloseIdleConnections()
	cancel() // as SIGTERM would
	if err := <-served; err != nil {
		t.Errorf("serveOn returned %v after cancel, want nil", err)
	}
}
