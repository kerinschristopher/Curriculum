package main

import (
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// metrics uses its own registry, not the library's global one, so each test starts from zero.
type metrics struct {
	reg     *prometheus.Registry
	latency *prometheus.HistogramVec
}

func newMetrics() *metrics {
	m := &metrics{
		reg: prometheus.NewRegistry(),
		// Also the request counter: its _count rises by one per request, with the same labels, so a separate
		// counter would hold the same numbers twice. Each route/method/code set costs 12 series (10 buckets
		// with +Inf, _sum, _count). DefBuckets without 5 s and 10 s: anything slower than 2.5 s is "+Inf".
		latency: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "http_request_duration_seconds",
			Help:    "Time to handle HTTP requests, by route, method and status code.",
			Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5},
		}, []string{"route", "method", "code"}),
	}
	m.reg.MustRegister(m.latency,
		collectors.NewGoCollector(),
		collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}),
	)
	return m
}

// handler serves the registry in Prometheus's text format.
func (m *metrics) handler() http.Handler {
	return promhttp.HandlerFor(m.reg, promhttp.HandlerOpts{})
}

// instrument counts and times every request mux handles.
func (m *metrics) instrument(mux *http.ServeMux) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec := &statusRecorder{ResponseWriter: w, code: http.StatusOK}
		start := time.Now()
		mux.ServeHTTP(rec, r)
		elapsed := time.Since(start).Seconds()
		labels := []string{routeOf(r), methodOf(r), strconv.Itoa(rec.code)}
		m.latency.WithLabelValues(labels...).Observe(elapsed)
	})
}

// routeOf is the route label: the path half of the pattern that served r ("GET /health" → "/health").
// mux.ServeHTTP sets r.Pattern, so call it only after ServeHTTP. "unmatched" means no pattern served r:
// an unknown path (404) or a known path with the wrong method (405, e.g. POST /health). A redirect to
// the cleaned path (//health → 307) carries the pattern it redirects to.
// It never uses the raw path, so a client sending random paths can't create new series (R8).
func routeOf(r *http.Request) string {
	if r.Pattern == "" {
		return "unmatched"
	}
	if _, path, found := strings.Cut(r.Pattern, " "); found {
		return path
	}
	return r.Pattern
}

// methodOf is the request's method if HTTP defines it, or "other". The client picks the method,
// and Go's server accepts any token, so a made-up one must not add series (R8).
func methodOf(r *http.Request) string {
	switch r.Method {
	case http.MethodGet, http.MethodHead, http.MethodPost, http.MethodPut, http.MethodPatch,
		http.MethodDelete, http.MethodConnect, http.MethodOptions, http.MethodTrace:
		return r.Method
	}
	return "other"
}

// statusRecorder remembers the status the client got: the first one the handler set, or 200 if it wrote a
// body first or never called WriteHeader. Go ignores (and logs) every WriteHeader after that (E9).
type statusRecorder struct {
	http.ResponseWriter
	code int
	sent bool
}

func (s *statusRecorder) WriteHeader(code int) {
	if !s.sent {
		s.code = code
		s.sent = true
	}
	s.ResponseWriter.WriteHeader(code)
}

// Write sends 200 if no status was set yet, and code already holds 200.
func (s *statusRecorder) Write(b []byte) (int, error) {
	s.sent = true
	return s.ResponseWriter.Write(b)
}

// Unwrap lets http.ResponseController reach the real writer (Flush, deadlines). A type assertion such as
// w.(http.Flusher) still fails on statusRecorder, so handlers use ResponseController.
func (s *statusRecorder) Unwrap() http.ResponseWriter { return s.ResponseWriter }
