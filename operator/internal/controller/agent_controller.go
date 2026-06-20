// Package controller contains the AgentReconciler that watches Agent CRs
// and orchestrates the corresponding chat-server Deployment + Service,
// plus the supporting ServiceAccount / ClusterRoleBinding / PVC.
//
// +kubebuilder:rbac:groups=agent.demo.io,resources=agents,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=agent.demo.io,resources=agents/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=core,resources=services,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=core,resources=serviceaccounts,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=core,resources=persistentvolumeclaims,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=clusterrolebindings,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=core,resources=secrets,verbs=get;list;watch
package controller

import (
	"context"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/builder"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	agentv1alpha1 "github.com/songli0103/kagent-ls/api/v1alpha1"
	"github.com/songli0103/kagent-ls/internal/deployment"
	"github.com/songli0103/kagent-ls/internal/services"
)

// Reconcile-loop requeue cadences. Centralised so the polling schedule
// is auditable in one place rather than scattered across observeDeployment.
const (
	// requeueAfterDeploymentCreate is the wait between creating the
	// Deployment and the next observe pass (give the API server a beat).
	requeueAfterDeploymentCreate = 3 * time.Second
	// requeueAfterCredentialRoll is the wait between a credential-driven
	// rollout and the next observe pass.
	requeueAfterCredentialRoll = 3 * time.Second
	// requeueAfterObservedReady is the steady-state poll cadence when
	// the Deployment is Ready (longer to reduce controller load).
	requeueAfterObservedReady = 10 * time.Second
	// requeueAfterObservedNotReady is the wait when the Deployment
	// exists but is not yet Ready.
	requeueAfterObservedNotReady = 5 * time.Second
)

// AgentReconciler reconciles Agent custom resources by creating and
// observing the chat-server Deployment + Service, the supporting
// ServiceAccount + ClusterRoleBindings, and (optionally) a PVC for chat
// history persistence.
type AgentReconciler struct {
	client.Client
	Scheme         *runtime.Scheme
	DepService     *services.DeploymentService
	StatusService  *services.StatusService
	RBACService    *services.RBACService
	StorageService *services.StorageService
	// DefaultCredentialsSecret is the operator-wide default for the
	// credentials Secret name; used to decide whether a Secret event is
	// relevant to any Agent.
	DefaultCredentialsSecret string
}

// SetupWithManager wires the reconciler into the controller-runtime manager.
// Watches two things:
//   - Agent CRs (primary resource).
//   - Secrets — the mapper below enqueues all Agent CRs in the same
//     namespace whose spec.credentialsSecret (or the default) matches
//     the changed Secret's name. That gives us credential-rotation
//     rollout for free.
func (r *AgentReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&agentv1alpha1.Agent{}).
		Watches(
			&corev1.Secret{},
			handler.EnqueueRequestsFromMapFunc(r.secretToAgents),
			builder.WithPredicates(secretDataChangedPredicate{}),
		).
		Complete(r)
}

// secretToAgents maps a Secret event to reconcile requests for all Agent
// CRs in the same namespace that reference this Secret (either explicitly
// via spec.credentialsSecret, or implicitly via the operator-wide default).
//
// Used for credential rotation: a `kubectl edit secret` flips the Secret's
// resourceVersion, this mapper fires for every affected Agent, the
// reconciler updates the pod-template annotation, and the Deployment
// rolls.
func (r *AgentReconciler) secretToAgents(ctx context.Context, obj client.Object) []reconcile.Request {
	secret, ok := obj.(*corev1.Secret)
	if !ok {
		return nil
	}
	agents := &agentv1alpha1.AgentList{}
	if err := r.List(ctx, agents, client.InNamespace(secret.Namespace)); err != nil {
		return nil
	}
	var reqs []reconcile.Request
	for i := range agents.Items {
		a := &agents.Items[i]
		if deployment.CredentialsSecretFor(a, r.DefaultCredentialsSecret) != secret.Name {
			continue
		}
		reqs = append(reqs, reconcile.Request{
			NamespacedName: types.NamespacedName{Namespace: a.Namespace, Name: a.Name},
		})
	}
	return reqs
}

// Reconcile observes the current Agent state and converges it towards the
// desired state.
//
// State machine:
//
//   - Always: reconcile SA + RBAC bindings + PVC (cheap, idempotent).
//     ReconcileRBAC owns the create-and-teardown-of-the-write-binding
//     decision end-to-end, so the controller doesn't have to know.
//   - Always when DeploymentRef is set: if the referenced credentials
//     Secret's resourceVersion differs from the pod-template annotation,
//     update the annotation to force a rolling restart (credential
//     rotation).
//   - (no DeploymentRef)        → create Deployment + Service, → Running
//   - (DeploymentRef, not ready) → requeue, wait for pods
//   - (DeploymentRef, ready)     → mark Ready, requeue to keep refs fresh
//   - (spec generation changed)  → delete old Deployment, rebuild
//   - (any error)                → mark Failed, requeue with backoff
func (r *AgentReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx).WithName("agent")

	agent := &agentv1alpha1.Agent{}
	if err := r.Get(ctx, req.NamespacedName, agent); err != nil {
		if apierrors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		logger.Error(err, "unable to fetch Agent")
		return ctrl.Result{}, err
	}

	if agent.DeletionTimestamp != nil {
		return ctrl.Result{}, nil
	}

	// 1) SA + RBAC bindings (ReconcileRBAC owns the create vs. teardown
	//    of the write binding).
	if err := r.RBACService.ReconcileRBAC(ctx, agent); err != nil {
		logger.Error(err, "reconcile rbac failed")
		return ctrl.Result{}, err
	}

	// 2) PVC (create when history.enabled, delete when off).
	if err := r.StorageService.ReconcilePVC(ctx, agent); err != nil {
		logger.Error(err, "reconcile pvc failed")
		return ctrl.Result{}, err
	}

	// 3) Spec changed since last reconcile: tear down the old deployment
	//    so a new one is built from the current spec.
	if agent.Status.DeploymentRef.Name != "" && agent.Status.ObservedGeneration != agent.Generation {
		logger.Info("spec generation changed, rebuilding deployment",
			"old_gen", agent.Status.ObservedGeneration, "new_gen", agent.Generation)
		if err := r.DepService.DeleteDeployment(ctx, agent); err != nil {
			logger.Error(err, "failed to delete old deployment")
			return ctrl.Result{}, err
		}
		if err := r.StatusService.ResetForRerun(ctx, agent); err != nil {
			logger.Error(err, "failed to reset status for re-run")
			return ctrl.Result{}, err
		}
		return ctrl.Result{Requeue: true}, nil
	}

	// 4) No deployment yet: create one.
	if agent.Status.DeploymentRef.Name == "" {
		return r.startDeployment(ctx, agent)
	}

	// 5) Deployment exists: observe it (and roll if creds changed).
	return r.observeDeployment(ctx, agent)
}

func (r *AgentReconciler) startDeployment(ctx context.Context, agent *agentv1alpha1.Agent) (ctrl.Result, error) {
	logger := log.FromContext(ctx).WithName("agent")
	if err := r.DepService.CreateDeployment(ctx, agent); err != nil {
		logger.Error(err, "failed to create deployment")
		if markErr := r.StatusService.MarkFailed(ctx, agent, err.Error()); markErr != nil {
			return ctrl.Result{}, markErr
		}
		return ctrl.Result{}, err
	}
	// Pass the deployment's name directly to MarkRunning — there's no
	// DeploymentRef in the status yet (we're about to set it), and
	// GetDeployment would look it up using the empty ref and fail.
	depName := deployment.DeploymentName(agent)
	if err := r.StatusService.MarkRunning(ctx, agent, depName); err != nil {
		logger.Error(err, "failed to mark running")
		return ctrl.Result{}, err
	}
	return ctrl.Result{RequeueAfter: requeueAfterDeploymentCreate}, nil
}

func (r *AgentReconciler) observeDeployment(ctx context.Context, agent *agentv1alpha1.Agent) (ctrl.Result, error) {
	logger := log.FromContext(ctx).WithName("agent")
	dep, err := r.DepService.GetDeployment(ctx, agent)
	if err != nil {
		if apierrors.IsNotFound(err) {
			// Deployment was deleted out-of-band; rebuild.
			logger.Info("deployment missing, recreating", "expected", agent.Status.DeploymentRef.Name)
			if err := r.StatusService.ResetForRerun(ctx, agent); err != nil {
				return ctrl.Result{}, err
			}
			return ctrl.Result{Requeue: true}, nil
		}
		return ctrl.Result{}, err
	}

	// Credential rotation: if the Secret's resourceVersion differs from
	// the pod-template annotation, update the annotation. The
	// Deployment controller treats any pod-template change as a rollout
	// trigger, so the running pods will be replaced with new ones that
	// have the updated envFrom-resolved credentials.
	if rolled, err := r.rollIfCredentialsChanged(ctx, agent, dep); err != nil {
		logger.Error(err, "credential rotation check failed")
		return ctrl.Result{}, err
	} else if rolled {
		logger.Info("credentials changed, rolling deployment")
		return ctrl.Result{RequeueAfter: requeueAfterCredentialRoll}, nil
	}

	ready := r.DepService.ReadyReplicas(dep)
	if r.DepService.IsReady(dep) {
		if agent.Status.Phase != agentv1alpha1.AgentPhaseReady || agent.Status.ReadyReplicas != ready {
			if err := r.StatusService.MarkReady(ctx, agent, ready); err != nil {
				return ctrl.Result{}, err
			}
		}
		return ctrl.Result{RequeueAfter: requeueAfterObservedReady}, nil
	}

	// Not ready yet — keep watching.
	return ctrl.Result{RequeueAfter: requeueAfterObservedNotReady}, nil
}

// rollIfCredentialsChanged checks the credentials Secret's
// resourceVersion against the pod-template annotation, and patches the
// annotation if they differ. Returns (true, nil) when a patch was made.
//
// Tolerates: missing Secret (return false — nothing to roll), missing
// annotation (treated as empty, so any Secret with non-empty RV rolls).
//
// The Update is wrapped in RetryOnConflict because a concurrent
// reconcile (e.g. a Secret watch landing a few ms before the agent
// reconcile) can move the resourceVersion out from under us.
func (r *AgentReconciler) rollIfCredentialsChanged(ctx context.Context, agent *agentv1alpha1.Agent, dep *appsv1.Deployment) (bool, error) {
	secretName := deployment.CredentialsSecretFor(agent, r.DefaultCredentialsSecret)
	secret := &corev1.Secret{}
	if err := r.Get(ctx, types.NamespacedName{Namespace: agent.Namespace, Name: secretName}, secret); err != nil {
		if apierrors.IsNotFound(err) {
			return false, nil
		}
		return false, err
	}
	rv := secret.ResourceVersion
	annotations := dep.Spec.Template.Annotations
	if current, ok := annotations[deployment.CredentialsVersionAnnotation]; ok && current == rv {
		return false, nil
	}
	updated := false
	err := services.RetryOnConflict(func() error {
		// Re-Get inside the retry: the deployment may have been mutated
		// by a parallel reconcile.
		fresh := &appsv1.Deployment{}
		if err := r.Get(ctx, types.NamespacedName{Namespace: dep.Namespace, Name: dep.Name}, fresh); err != nil {
			return err
		}
		ann := fresh.Spec.Template.Annotations
		if cur, ok := ann[deployment.CredentialsVersionAnnotation]; ok && cur == rv {
			updated = false
			return nil
		}
		if ann == nil {
			ann = map[string]string{}
		}
		ann[deployment.CredentialsVersionAnnotation] = rv
		fresh.Spec.Template.Annotations = ann
		if err := r.Update(ctx, fresh); err != nil {
			return err
		}
		updated = true
		return nil
	})
	return updated, err
}
