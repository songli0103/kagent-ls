package services

import (
	"context"
	"fmt"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"

	agentv1alpha1 "github.com/songli0103/kagent-ls/api/v1alpha1"
	"github.com/songli0103/kagent-ls/internal/deployment"
)

// StatusService writes lifecycle transitions to the Agent status subresource.
type StatusService struct {
	client client.Client
}

// NewStatusService constructs a StatusService.
func NewStatusService(c client.Client) *StatusService {
	return &StatusService{client: c}
}

// MarkPending records that the controller has begun materialising resources
// for the agent's current spec generation.
func (s *StatusService) MarkPending(ctx context.Context, agent *agentv1alpha1.Agent, reason string) error {
	now := metav1.Now()
	return s.updateRetry(ctx, agent, func() {
		agent.Status.Phase = agentv1alpha1.AgentPhasePending
		agent.Status.ObservedGeneration = agent.Generation
		agent.Status.Conditions = appendOrReplaceCondition(agent.Status.Conditions, agentv1alpha1.AgentCondition{
			Type:               "Pending",
			Status:             "True",
			LastTransitionTime: now,
			Reason:             "Pending",
			Message:            reason,
		})
	})
}

// MarkRunning records that the Deployment has been created and the
// controller is now waiting for it to become ready. `depName` is the
// deterministic deployment name (see deployment.DeploymentName) — we
// accept it as a string so the caller doesn't need a live Deployment
// object to mark status.
func (s *StatusService) MarkRunning(ctx context.Context, agent *agentv1alpha1.Agent, depName string) error {
	now := metav1.Now()
	return s.updateRetry(ctx, agent, func() {
		agent.Status.Phase = agentv1alpha1.AgentPhaseRunning
		agent.Status.DeploymentRef.Name = depName
		agent.Status.ServiceRef.Name = deployment.ServiceName(agent)
		agent.Status.ObservedGeneration = agent.Generation
		agent.Status.Conditions = appendOrReplaceCondition(agent.Status.Conditions, agentv1alpha1.AgentCondition{
			Type:               "Running",
			Status:             "True",
			LastTransitionTime: now,
			Reason:             "DeploymentCreated",
			Message:            fmt.Sprintf("created deployment %s for generation %d", depName, agent.Generation),
		})
	})
}

// MarkReady records that the Deployment's pod is up and the chat server is
// responding on /ready. Called repeatedly as the ready replica count
// changes; only the first call after a restart stamps StartedAt.
func (s *StatusService) MarkReady(ctx context.Context, agent *agentv1alpha1.Agent, readyReplicas int32) error {
	now := metav1.Now()
	return s.updateRetry(ctx, agent, func() {
		agent.Status.Phase = agentv1alpha1.AgentPhaseReady
		agent.Status.ReadyReplicas = readyReplicas
		agent.Status.ObservedGeneration = agent.Generation
		if agent.Status.StartedAt == nil {
			agent.Status.StartedAt = &now
		}
		agent.Status.Conditions = appendOrReplaceCondition(agent.Status.Conditions, agentv1alpha1.AgentCondition{
			Type:               "Ready",
			Status:             "True",
			LastTransitionTime: now,
			Reason:             "DeploymentReady",
			Message:            fmt.Sprintf("%d pod(s) ready, chat server accepting requests", readyReplicas),
		})
	})
}

// MarkFailed records a non-recoverable failure.
func (s *StatusService) MarkFailed(ctx context.Context, agent *agentv1alpha1.Agent, reason string) error {
	now := metav1.Now()
	return s.updateRetry(ctx, agent, func() {
		agent.Status.Phase = agentv1alpha1.AgentPhaseFailed
		agent.Status.ObservedGeneration = agent.Generation
		agent.Status.Conditions = appendOrReplaceCondition(agent.Status.Conditions, agentv1alpha1.AgentCondition{
			Type:               "Failed",
			Status:             "True",
			LastTransitionTime: now,
			Reason:             "DeploymentFailed",
			Message:            reason,
		})
	})
}

// ResetForRerun clears the deployment/service refs so the next reconcile
// rebuilds them. Used when spec generation changes.
func (s *StatusService) ResetForRerun(ctx context.Context, agent *agentv1alpha1.Agent) error {
	return s.updateRetry(ctx, agent, func() {
		agent.Status.Phase = agentv1alpha1.AgentPhasePending
		agent.Status.DeploymentRef = agentv1alpha1.AgentDeploymentRef{}
		agent.Status.ServiceRef = agentv1alpha1.AgentServiceRef{}
		agent.Status.ReadyReplicas = 0
		agent.Status.ObservedGeneration = 0
		agent.Status.StartedAt = nil
	})
}

// updateRetry wraps Get+mutate+Status().Update in RetryOnConflict to
// be robust against concurrent reconciles (e.g. when a Secret watch
// fires during a status write).
func (s *StatusService) updateRetry(ctx context.Context, agent *agentv1alpha1.Agent, mutate func()) error {
	return RetryOnConflict(func() error {
		key := client.ObjectKeyFromObject(agent)
		// Use the cached reader for the Get inside the retry, but the
		// controller-runtime client's Get uses the cache by default; the
		// Status().Update is always against the API server.
		if err := s.client.Get(ctx, key, agent); err != nil {
			return err
		}
		mutate()
		return s.client.Status().Update(ctx, agent)
	})
}

func appendOrReplaceCondition(conditions []agentv1alpha1.AgentCondition, c agentv1alpha1.AgentCondition) []agentv1alpha1.AgentCondition {
	for i, existing := range conditions {
		if existing.Type == c.Type {
			conditions[i] = c
			return conditions
		}
	}
	return append(conditions, c)
}
