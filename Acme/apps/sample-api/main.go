package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"time"
)

type healthResponse struct {
	Status  string `json:"status"`
	Version string `json:"version"`
}

func getEnvOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// healthHandler builds the /health handler. version and logLevel are passed in
// (rather than read from globals) so tests can control them directly.
func healthHandler(version, logLevel string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if logLevel == "debug" {
			log.Printf("request: %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(healthResponse{Status: "ok", Version: version}); err != nil {
			log.Printf("encode response: %v", err)
		}
	}
}

func main() {
	version := getEnvOrDefault("APP_VERSION", "0.1.0")
	logLevel := getEnvOrDefault("LOG_LEVEL", "info")

	mux := http.NewServeMux()
	mux.HandleFunc("/health", healthHandler(version, logLevel))

	srv := &http.Server{
		Addr:              ":8080",
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}

	log.Printf("starting sample-api: version=%s log_level=%s addr=%s", version, logLevel, srv.Addr)
	log.Fatal(srv.ListenAndServe())
}
