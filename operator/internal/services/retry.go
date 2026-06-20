// Package services — retry.go holds tiny shared helpers used by the
// per-service structs.
package services

import (
	"k8s.io/client-go/util/retry"
)

// RetryOnConflict wraps `op` in retry.RetryOnConflict. Use for any
// Get-mutate-Update pattern against the apiserver: concurrent reconciles
// (e.g. a Secret watch firing in the middle of a status write) are
// common in this controller and would otherwise surface as spurious
// conflict errors. The closure can capture any needed context from its
// enclosing scope.
//
// Exported so the controller package can use the same retry policy as
// the per-service status writers.
func RetryOnConflict(op func() error) error {
	return retry.RetryOnConflict(retry.DefaultRetry, op)
}
