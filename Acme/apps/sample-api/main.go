package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"strings"
)

var version = getEnvOrDefault("APP_VERSION", "0.1.1")

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
	// slog parses debug|info|warn|error case-insensitively and errors on anything else.
	// Fail fast on a bad value rather than silently running at the wrong level.
	raw := getEnvOrDefault("LOG_LEVEL", "info")
	var level slog.Level
	if err := level.UnmarshalText([]byte(strings.TrimSpace(raw))); err != nil {
		fmt.Fprintf(os.Stderr, "invalid LOG_LEVEL %q: must be one of debug, info, warn, error\n", raw)
		os.Exit(1)
	}

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level}))
	slog.SetDefault(logger)

	// Emit at no lower than the configured level so the resolved level is always visible.
	logger.Log(context.Background(), max(slog.LevelInfo, level), "starting",
		"version", version, "log_level", level.String())

	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		logger.Debug("request", "method", r.Method, "path", r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(healthResponse{
			Status:  "ok",
			Version: version,
		})
	})

	if err := http.ListenAndServe(":8080", nil); err != nil {
		logger.Error("server exited", "err", err)
		os.Exit(1)
	}
}
