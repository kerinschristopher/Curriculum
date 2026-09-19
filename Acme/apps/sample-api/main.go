package main

import (
	"encoding/json"
	"net/http"
)

const version = "0.1.0"

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