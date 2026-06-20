// Package deployment builds the Kubernetes Deployment + Service that
// host the long-running agent chat server.
package deployment

import (
	"fmt"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"

	agentv1alpha1 "github.com/songli0103/kagent-ls/api/v1alpha1"
)

// Annotation keys stamped on operator-owned resources.
const (
	// CredentialsVersionAnnotation is set on the Deployment pod
	// template to the Secret's resourceVersion. Any change to the
	// pod template triggers a rolling restart, so this is the
	// mechanism we use to roll on credential rotation.
	CredentialsVersionAnnotation = "agent.demo.io/credentials-version"
	// RoleAnnotationKey records which ClusterRole a given
	// ClusterRoleBinding grants. Helpful when listing the bindings
	// owned by an Agent.
	RoleAnnotationKey = "agent.demo.io/role"
)

// HistoryMountPath is the in-container mount point the runtime reads
// for session files. The runtime reads RUNTIME_HISTORY_DIR, which the
// operator sets to this path.
const HistoryMountPath = "/var/lib/agent-runtime/history"

// HistoryVolumeName is the name of the volume / mount for the chat
// history PVC. Kept as a constant so the volume and its mount agree.
const HistoryVolumeName = "history"

// RUNTIME_HISTORY_DIR is the env var the runtime reads to enable
// persistence. Set only when spec.history.enabled is true.
const RuntimeHistoryDirEnvVar = "RUNTIME_HISTORY_DIR"

// Builder produces a Deployment and Service for a given Agent CR.
type Builder struct {
	// DefaultCredentialsSecret is used when an Agent does not set
	// spec.credentialsSecret. Kept on the builder so the controller
	// manager can set it from a CLI flag.
	DefaultCredentialsSecret string
}

// NewBuilder returns a Builder with default settings.
func NewBuilder(defaultCredentialsSecret string) *Builder {
	return &Builder{DefaultCredentialsSecret: defaultCredentialsSecret}
}

// CredentialsSecretFor returns the Secret name an Agent should use for
// LLM credentials: spec.credentialsSecret takes priority, falling back
// to the operator-wide default if unset/empty. Free function so the
// controller, the builder, and the secret-event mapper can all resolve
// the same way.
func CredentialsSecretFor(agent *agentv1alpha1.Agent, defaultName string) string {
	if agent.Spec.CredentialsSecret != nil && *agent.Spec.CredentialsSecret != "" {
		return *agent.Spec.CredentialsSecret
	}
	return defaultName
}

// LabelsFor returns the standard {agent, component=runtime} label set.
// Free function so the per-Agent services (rbac, storage) can stamp the
// same labels on the resources they create.
func LabelsFor(agent *agentv1alpha1.Agent) map[string]string {
	return map[string]string{
		AgentLabelKey:     agent.Name,
		ComponentLabelKey: ComponentValue,
	}
}

// ServiceAccountName returns the SA the runtime pod runs as. One SA per
// Agent CR (named after the agent) keeps RBAC bindings tight and visible.
func ServiceAccountName(agent *agentv1alpha1.Agent) string {
	return fmt.Sprintf("%s-agent-runtime", agent.Name)
}

// PVCName returns the PVC name used for chat history persistence.
func PVCName(agent *agentv1alpha1.Agent) string {
	return fmt.Sprintf("%s-history", agent.Name)
}

// RoleBindingName returns the name of the per-Agent ClusterRoleBinding
// for the given role suffix (e.g. "read" or "write").
func RoleBindingName(agent *agentv1alpha1.Agent, suffix string) string {
	return fmt.Sprintf("%s-agent-runtime-%s", agent.Name, suffix)
}

// Suffixes used in RoleBindingName.
const (
	ReadBindingSuffix  = "read"
	WriteBindingSuffix = "write"
)

// Deployment returns the Deployment that runs the agent runtime for the
// given Agent CR. The Deployment is owned by the Agent (via OwnerReference
// set by the caller) so deleting the Agent GC's the Deployment.
func (b *Builder) Deployment(agent *agentv1alpha1.Agent) *appsv1.Deployment {
	labels := LabelsFor(agent)
	replicas := int32(1)
	credName := CredentialsSecretFor(agent, b.DefaultCredentialsSecret)

	container := buildContainer(agent, credName)
	podSpec := corev1.PodSpec{
		ServiceAccountName: ServiceAccountName(agent),
		Containers:         []corev1.Container{container},
	}
	if agent.Spec.History != nil && agent.Spec.History.Enabled {
		applyHistory(agent, &podSpec)
	}

	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      DeploymentName(agent),
			Namespace: agent.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: &replicas,
			Selector: &metav1.LabelSelector{MatchLabels: labels},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec:       podSpec,
			},
		},
	}
}

// buildContainer assembles the main runtime container with its ports,
// resources, env, and probes. Pulled out of Deployment() so the fields
// are individually testable and the constants live in one place.
func buildContainer(agent *agentv1alpha1.Agent, credentialsSecret string) corev1.Container {
	return corev1.Container{
		Name:  ContainerName,
		Image: agent.Spec.Runtime.Image,
		Ports: []corev1.ContainerPort{{
			Name:          PortName,
			ContainerPort: Port,
			Protocol:      corev1.ProtocolTCP,
		}},
		EnvFrom: envFromSecret(credentialsSecret),
		Resources: corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse(DefaultCPURequest),
				corev1.ResourceMemory: resource.MustParse(DefaultMemoryRequest),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse(DefaultCPULimit),
				corev1.ResourceMemory: resource.MustParse(DefaultMemoryLimit),
			},
		},
		ReadinessProbe: &corev1.Probe{
			ProbeHandler: corev1.ProbeHandler{
				HTTPGet: &corev1.HTTPGetAction{
					Path: "/ready",
					Port: intstr.FromInt32(Port),
				},
			},
			InitialDelaySeconds: ReadinessInitialDelay,
			PeriodSeconds:       ReadinessPeriod,
		},
		LivenessProbe: &corev1.Probe{
			ProbeHandler: corev1.ProbeHandler{
				HTTPGet: &corev1.HTTPGetAction{
					Path: "/health",
					Port: intstr.FromInt32(Port),
				},
			},
			InitialDelaySeconds: LivenessInitialDelay,
			PeriodSeconds:       LivenessPeriod,
		},
	}
}

// applyHistory wires the history PVC into podSpec: the env var pointing
// the runtime at the mount path, the volumeMount on the main container,
// and the volume on the pod. One function so the on/off decision and
// the three wiring steps live in one place. Operates only on podSpec —
// callers don't have to track a separate container pointer.
func applyHistory(agent *agentv1alpha1.Agent, podSpec *corev1.PodSpec) {
	for i := range podSpec.Containers {
		if podSpec.Containers[i].Name != ContainerName {
			continue
		}
		podSpec.Containers[i].Env = append(podSpec.Containers[i].Env, corev1.EnvVar{
			Name:  RuntimeHistoryDirEnvVar,
			Value: HistoryMountPath,
		})
		podSpec.Containers[i].VolumeMounts = append(podSpec.Containers[i].VolumeMounts, corev1.VolumeMount{
			Name:      HistoryVolumeName,
			MountPath: HistoryMountPath,
		})
	}
	podSpec.Volumes = append(podSpec.Volumes, corev1.Volume{
		Name: HistoryVolumeName,
		VolumeSource: corev1.VolumeSource{
			PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
				ClaimName: PVCName(agent),
			},
		},
	})
}

// Service returns the ClusterIP Service that fronts the runtime Deployment.
func (b *Builder) Service(agent *agentv1alpha1.Agent) *corev1.Service {
	labels := LabelsFor(agent)
	return &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      ServiceName(agent),
			Namespace: agent.Namespace,
			Labels:    labels,
		},
		Spec: corev1.ServiceSpec{
			Type:     corev1.ServiceTypeClusterIP,
			Selector: labels,
			Ports: []corev1.ServicePort{{
				Name:       "http",
				Port:       Port,
				TargetPort: intstr.FromInt32(Port),
				Protocol:   corev1.ProtocolTCP,
			}},
		},
	}
}

// envFromSecret returns the envFrom list for a Secret, or nil if name is
// empty. We deliberately do not return a single-element slice with an
// empty name — that would mount the *literal* empty-named Secret (which
// would fail at admission time) rather than skipping envFrom entirely.
func envFromSecret(name string) []corev1.EnvFromSource {
	if name == "" {
		return nil
	}
	return []corev1.EnvFromSource{{
		SecretRef: &corev1.SecretEnvSource{
			LocalObjectReference: corev1.LocalObjectReference{Name: name},
		},
	}}
}

// DeploymentName returns the deterministic name of the Deployment for an Agent.
func DeploymentName(agent *agentv1alpha1.Agent) string {
	return fmt.Sprintf("%s-runtime", agent.Name)
}

// ServiceName returns the deterministic name of the Service for an Agent.
func ServiceName(agent *agentv1alpha1.Agent) string {
	return fmt.Sprintf("%s-runtime", agent.Name)
}
