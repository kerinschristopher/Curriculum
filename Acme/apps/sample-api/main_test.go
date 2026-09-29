package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthHandler(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()

	healthHandler("1.2.3", "info")(rec, req)

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

func TestGetEnvOrDefault(t *testing.T) {
	const key = "SAMPLE_API_TEST_VAR"

	t.Setenv(key, "from-env")
	if got := getEnvOrDefault(key, "fallback"); got != "from-env" {
		t.Errorf("with var set: got %q, want %q", got, "from-env")
	}

	t.Setenv(key, "")
	if got := getEnvOrDefault(key, "fallback"); got != "fallback" {
		t.Errorf("with var empty: got %q, want %q", got, "fallback")
	}
}
