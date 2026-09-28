package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
)

var version = getEnvOrDefault("APP_VERSION", "0.1.0")
var logLevel = getEnvOrDefault("LOG_LEVEL", "info")

func getEnvOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

type healthResponse struct {
	Status  string `json:"status"`
	Version string `json:"version"`
}

func main() {
	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		if logLevel == "debug" {
			log.Printf("request: %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(healthResponse{
			Status:  "ok",
			Version: version,
		})
	})
	http.ListenAndServe(":8080", nil)
}