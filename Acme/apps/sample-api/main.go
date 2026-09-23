package main

import (
	"encoding/json"
	"net/http"
	"os"
)

var version = getEnvOrDefault("APP_VERSION", "0.1.0")

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
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(healthResponse{
			Status:  "ok",
			Version: version,
		})
	})
	http.ListenAndServe(":8080", nil)
}