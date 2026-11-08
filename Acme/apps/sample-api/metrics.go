package main

import (
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// metrics uses its own registry, not the library's global one, so each test starts from zero.
type metrics struct {
	reg      *prometheus.Registry
	requests *prometheus.CounterVec
	latency  *prometheus.HistogramVec
}

func newMetrics() *metrics {
	m := &metrics{
		reg: prometheus.NewRegistry(),
		requests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "http_requests_total",
			Help: "HTTP requests handled, by route, method and status code.",
		}, []string{"route", "method", "code"}),
		// DefBuckets without 5 s and 10 s: anything slower than 2.5 s is "+Inf" for this API.
		latency: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "http_request_duration_seconds",
			Help:    "Time to handle HTTP requests, by route, method and status code.",
			Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5},
		}, []string{"route", "method", "code"}),
	}
	m.reg.MustRegister(m.requests, m.latency)
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

		labels := []string{routeOf(mux, r), methodOf(r), strconv.Itoa(rec.code)}
		m.requests.WithLabelValues(labels...).Inc()
		m.latency.WithLabelValues(labels...).Observe(elapsed)
	})
}

// routeOf is the path half of the pattern the mux matched ("GET /health" → "/health"), or "unmatched".
// It never uses the raw path, so a client sending random paths can't create new series (R8).
func routeOf(mux *http.ServeMux, r *http.Request) string {
	_, pattern := mux.Handler(r)
	if pattern == "" {
		return "unmatched"
	}
	if _, path, found := strings.Cut(pattern, " "); found {
		return path
	}
	return pattern
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

// statusRecorder remembers the status a handler sent; one that never calls WriteHeader sent 200.
type statusRecorder struct {
	http.ResponseWriter
	code int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.code = code
	s.ResponseWriter.WriteHeader(code)
}
