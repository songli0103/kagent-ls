package services

import (
	"errors"
	"testing"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime/schema"
)

// TestRetryOnConflict_RetriesOnConflictAndEventuallySucceeds verifies
// that the helper retries on IsConflict errors and stops on success.
func TestRetryOnConflict_RetriesOnConflictAndEventuallySucceeds(t *testing.T) {
	t.Parallel()
	conflict := apierrors.NewConflict(schema.GroupResource{Group: "agent.demo.io", Resource: "agents"}, "x", errors.New("changed"))
	calls := 0
	err := RetryOnConflict(func() error {
		calls++
		if calls < 3 {
			return conflict
		}
		return nil
	})
	if err != nil {
		t.Fatalf("expected nil after retries, got %v", err)
	}
	if calls != 3 {
		t.Fatalf("expected 3 attempts, got %d", calls)
	}
}

// TestRetryOnConflict_NonConflictErrorReturned verifies that the helper
// does NOT retry on errors that aren't conflict errors — surfaces them
// immediately.
func TestRetryOnConflict_NonConflictErrorReturned(t *testing.T) {
	t.Parallel()
	calls := 0
	want := errors.New("boom")
	err := RetryOnConflict(func() error {
		calls++
		return want
	})
	if !errors.Is(err, want) {
		t.Fatalf("expected %v, got %v", want, err)
	}
	if calls != 1 {
		t.Fatalf("expected 1 attempt, got %d", calls)
	}
}
