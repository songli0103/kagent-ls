// Package controller — predicates.go defines event filters that decide
// which Secret events are interesting to the Agent controller.
//
// We only want to enqueue an Agent when the *data* of a Secret changes
// (e.g. someone rotated LLM_API_KEY), not on every metadata-only update
// (labels, annotations, resourceVersion bumps from k8s itself, etc.).
package controller

import (
	corev1 "k8s.io/api/core/v1"
	"sigs.k8s.io/controller-runtime/pkg/event"
	"sigs.k8s.io/controller-runtime/pkg/predicate"
)

// secretDataChangedPredicate fires only on Create / Update events whose
// Secret payload (Data) actually changed. Deletions are ignored — the
// Agent CR still references the Secret by name, and the next reconcile
// will surface a "Secret not found" error.
type secretDataChangedPredicate struct {
	predicate.Funcs
}

func (secretDataChangedPredicate) Create(e event.CreateEvent) bool {
	return e.Object != nil
}

func (secretDataChangedPredicate) Update(e event.UpdateEvent) bool {
	if e.ObjectOld == nil || e.ObjectNew == nil {
		return false
	}
	oldSec, okOld := e.ObjectOld.(*corev1.Secret)
	newSec, okNew := e.ObjectNew.(*corev1.Secret)
	if !okOld || !okNew {
		return false
	}
	// Fast path: resourceVersion unchanged → no-op update from apiserver
	// (status, finalizer, etc.); skip.
	if oldSec.ResourceVersion == newSec.ResourceVersion {
		return false
	}
	// Trigger when Data or StringData changed. Comparing Data byte-for-
	// byte is fine — Secrets are small (<1KB typically) and the
	// controller only does a List+Map per change.
	return !equalByteMap(oldSec.Data, newSec.Data) || !equalStringMap(oldSec.StringData, newSec.StringData)
}

func (secretDataChangedPredicate) Delete(e event.DeleteEvent) bool {
	return false
}

// equalByteMap reports whether two byte maps are element-wise equal.
// We avoid reflect.DeepEqual because maps of []byte are not comparable
// with == in Go (you'd be comparing slice headers, not contents).
func equalByteMap(a, b map[string][]byte) bool {
	if len(a) != len(b) {
		return false
	}
	for k, av := range a {
		bv, ok := b[k]
		if !ok {
			return false
		}
		if string(av) != string(bv) {
			return false
		}
	}
	return true
}

func equalStringMap(a, b map[string]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, av := range a {
		if b[k] != av {
			return false
		}
	}
	return true
}
