// Package services contains the business-logic wrappers the controller
// calls. RBACService manages the per-Agent ServiceAccount and the
// ClusterRoleBindings that grant it read (and optionally write) access
// to the cluster.
package services

import (
	"context"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"

	agentv1alpha1 "github.com/songli0103/kagent-ls/api/v1alpha1"
	"github.com/songli0103/kagent-ls/internal/deployment"
)

// Cluster-wide role names. These match the manifests under
// operator/config/runtime/ — the operator only creates Bindings, not the
// ClusterRoles themselves (ClusterRoles are cluster-scoped and belong to
// the platform team that owns the cluster).
const (
	ReadClusterRole  = "agent-runtime"
	WriteClusterRole = "agent-runtime-write"
)

// RBACService manages ServiceAccount + ClusterRoleBinding lifecycle for
// an Agent. The bindings are cluster-scoped (ClusterRoleBinding) because
// the existing ClusterRoles are cluster-scoped; the SA itself is
// namespace-scoped (per Agent, named after the agent).
type RBACService struct {
	client client.Client
}

// NewRBACService constructs an RBACService.
func NewRBACService(c client.Client) *RBACService {
	return &RBACService{client: c}
}

// ReconcileRBAC is the entry point. It converges the SA + read binding +
// (conditional) write binding towards the spec:
//
//   - The SA and read binding are always present.
//   - The write binding is present only when spec.writeEnabled is true
//     and is removed (idempotently) when the flag flips off.
//
// The SA and its bindings are owned by the Agent so deleting the Agent
// garbage-collects them; the bindings are not, because they're
// cluster-scoped and the underlying ClusterRoles are managed elsewhere.
func (s *RBACService) ReconcileRBAC(ctx context.Context, agent *agentv1alpha1.Agent) error {
	if err := s.createServiceAccount(ctx, agent); err != nil {
		return fmt.Errorf("create serviceaccount: %w", err)
	}
	if err := s.createRoleBinding(ctx, agent, ReadClusterRole, deployment.ReadBindingSuffix); err != nil {
		return fmt.Errorf("create read rolebinding: %w", err)
	}
	if agent.Spec.WriteEnabled {
		if err := s.createRoleBinding(ctx, agent, WriteClusterRole, deployment.WriteBindingSuffix); err != nil {
			return fmt.Errorf("create write rolebinding: %w", err)
		}
		return nil
	}
	if err := s.deleteRoleBinding(ctx, agent, deployment.WriteBindingSuffix); err != nil {
		return fmt.Errorf("delete write rolebinding: %w", err)
	}
	return nil
}

// createServiceAccount creates the per-Agent SA. Idempotent.
func (s *RBACService) createServiceAccount(ctx context.Context, agent *agentv1alpha1.Agent) error {
	sa := &corev1.ServiceAccount{
		ObjectMeta: metav1.ObjectMeta{
			Name:      deployment.ServiceAccountName(agent),
			Namespace: agent.Namespace,
		},
	}
	if err := controllerutil.SetControllerReference(agent, sa, s.client.Scheme()); err != nil {
		return err
	}
	if err := s.client.Create(ctx, sa); err != nil {
		if apierrors.IsAlreadyExists(err) {
			return nil
		}
		return err
	}
	return nil
}

// createRoleBinding creates a ClusterRoleBinding named after the agent
// that grants the agent's SA the given cluster role. Idempotent: tolerates
// AlreadyExists. The cluster role is expected to already exist (we never
// create ClusterRoles here).
func (s *RBACService) createRoleBinding(ctx context.Context, agent *agentv1alpha1.Agent, clusterRole, suffix string) error {
	binding := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{
			Name:   deployment.RoleBindingName(agent, suffix),
			Labels: deployment.LabelsFor(agent),
			Annotations: map[string]string{
				deployment.RoleAnnotationKey: clusterRole,
			},
		},
		RoleRef: rbacv1.RoleRef{
			APIGroup: rbacv1.GroupName,
			Kind:     "ClusterRole",
			Name:     clusterRole,
		},
		Subjects: []rbacv1.Subject{{
			Kind:      rbacv1.ServiceAccountKind,
			Name:      deployment.ServiceAccountName(agent),
			Namespace: agent.Namespace,
		}},
	}
	if err := s.client.Create(ctx, binding); err != nil {
		if apierrors.IsAlreadyExists(err) {
			return nil
		}
		return err
	}
	return nil
}

// deleteRoleBinding removes the per-Agent ClusterRoleBinding for the
// given suffix. Idempotent (NotFound → nil). We do Get-then-Delete
// rather than Delete-by-name so the API server's referential-integrity
// check is explicit; a missing binding is treated as success.
func (s *RBACService) deleteRoleBinding(ctx context.Context, agent *agentv1alpha1.Agent, suffix string) error {
	name := deployment.RoleBindingName(agent, suffix)
	binding := &rbacv1.ClusterRoleBinding{}
	if err := s.client.Get(ctx, types.NamespacedName{Name: name}, binding); err != nil {
		if apierrors.IsNotFound(err) {
			return nil
		}
		return err
	}
	return s.client.Delete(ctx, binding)
}
