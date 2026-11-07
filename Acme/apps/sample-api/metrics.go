package main

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// metrics uses its own registry, not the library's global one, so each test starts from zero.
type metrics struct {
	reg      *prometheus.Registry
	requests *prometheus.CounterVec
}

func newMetrics() *metrics {
	m := &metrics{
		reg: prometheus.NewRegistry(),
		requests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "http_requests_total",
			Help: "HTTP requests handled, by route, method and status code.",
		}, []string{"route", "method", "code"}),
	}
	m.reg.MustRegister(m.requests)
	return m
}

// handler serves the registry in Prometheus's text format.
func (m *metrics) handler() http.Handler {
	return promhttp.HandlerFor(m.reg, promhttp.HandlerOpts{})
}

// instrument counts every request mux handles.
func (m *metrics) instrument(mux *http.ServeMux) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec := &statusRecorder{ResponseWriter: w, code: http.StatusOK}
		mux.ServeHTTP(rec, r)
		m.requests.WithLabelValues(routeOf(mux, r), r.Method, strconv.Itoa(rec.code)).Inc()
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

// statusRecorder remembers the status a handler sent; one that never calls WriteHeader sent 200.
type statusRecorder struct {
	http.ResponseWriter
	code int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.code = code
	s.ResponseWriter.WriteHeader(code)
}
