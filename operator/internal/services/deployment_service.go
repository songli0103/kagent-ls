// Package services contains the business-logic wrappers the controller
// calls. DeploymentService manages the Deployment + Service pair for an
// Agent, including create, get, readiness check and delete.
package services

import (
	"context"
	"fmt"

	appsv1 "k8s.io/api/apps/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"

	agentv1alpha1 "github.com/songli0103/kagent-ls/api/v1alpha1"
	"github.com/songli0103/kagent-ls/internal/deployment"
)

// DeploymentService manages the lifecycle of the runtime Deployment and
// Service that backs an Agent.
type DeploymentService struct {
	client  client.Client
	builder *deployment.Builder
}

// NewDeploymentService returns a DeploymentService using the given builder.
func NewDeploymentService(c client.Client, builder *deployment.Builder) *DeploymentService {
	return &DeploymentService{client: c, builder: builder}
}

// CreateDeployment creates the Deployment + Service for the agent. Sets
// OwnerReference so the resources are GC'd when the Agent is deleted.
func (s *DeploymentService) CreateDeployment(ctx context.Context, agent *agentv1alpha1.Agent) error {
	dep := s.builder.Deployment(agent)
	if err := controllerutil.SetControllerReference(agent, dep, s.client.Scheme()); err != nil {
		return fmt.Errorf("set controller reference on deployment: %w", err)
	}
	if err := s.client.Create(ctx, dep); err != nil {
		if apierrors.IsAlreadyExists(err) {
			return nil
		}
		return fmt.Errorf("create deployment: %w", err)
	}

	svc := s.builder.Service(agent)
	if err := controllerutil.SetControllerReference(agent, svc, s.client.Scheme()); err != nil {
		return fmt.Errorf("set controller reference on service: %w", err)
	}
	if err := s.client.Create(ctx, svc); err != nil {
		if apierrors.IsAlreadyExists(err) {
			return nil
		}
		return fmt.Errorf("create service: %w", err)
	}
	return nil
}

// GetDeployment returns the Deployment referenced by the agent.
func (s *DeploymentService) GetDeployment(ctx context.Context, agent *agentv1alpha1.Agent) (*appsv1.Deployment, error) {
	dep := &appsv1.Deployment{}
	key := types.NamespacedName{Namespace: agent.Namespace, Name: agent.Status.DeploymentRef.Name}
	if err := s.client.Get(ctx, key, dep); err != nil {
		return nil, err
	}
	return dep, nil
}

// IsReady returns true when the Deployment has at least one ready replica.
func (s *DeploymentService) IsReady(dep *appsv1.Deployment) bool {
	return dep != nil && dep.Status.ReadyReplicas > 0
}

// ReadyReplicas returns the number of ready replicas.
func (s *DeploymentService) ReadyReplicas(dep *appsv1.Deployment) int32 {
	if dep == nil {
		return 0
	}
	return dep.Status.ReadyReplicas
}

// DeleteDeployment removes the Deployment (the Service is GC'd by owner
// reference). Tolerates NotFound.
func (s *DeploymentService) DeleteDeployment(ctx context.Context, agent *agentv1alpha1.Agent) error {
	dep := &appsv1.Deployment{}
	key := types.NamespacedName{Namespace: agent.Namespace, Name: deployment.DeploymentName(agent)}
	if err := s.client.Get(ctx, key, dep); err != nil {
		if apierrors.IsNotFound(err) {
			return nil
		}
		return fmt.Errorf("get deployment for delete: %w", err)
	}
	// Setting propagation policy to Background makes the deletion return
	// immediately; we don't need to wait for pods to terminate.
	if err := s.client.Delete(ctx, dep); err != nil {
		if apierrors.IsNotFound(err) {
			return nil
		}
		return fmt.Errorf("delete deployment: %w", err)
	}
	return nil
}
