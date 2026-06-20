package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
)

// AgentPhase describes the phase of an Agent execution.
type AgentPhase string

const (
	AgentPhasePending   AgentPhase = "Pending"
	AgentPhaseRunning   AgentPhase = "Running"
	AgentPhaseReady     AgentPhase = "Ready"
	AgentPhaseSucceeded AgentPhase = "Succeeded"
	AgentPhaseFailed    AgentPhase = "Failed"
	AgentPhaseUnknown   AgentPhase = "Unknown"
)

// AgentRuntimeSpec defines the container runtime used to execute the task.
type AgentRuntimeSpec struct {
	// Image is the container image that the Operator will run inside the
	// Deployment. The image must expose an HTTP server on port 8080.
	Image string `json:"image"`
}

// AgentTaskSpec describes the task the agent should perform.
//
// In the long-running chat service mode (the default since v0.2) the
// runtime ignores these fields; the chat is driven by HTTP requests to
// /chat/completions on the runtime container. The fields are kept for
// backward compatibility with existing Agent CRs.
type AgentTaskSpec struct {
	// Tool selects which tool the agent should execute.
	// +optional
	// +kubebuilder:validation:Enum=cluster-inspect
	Tool string `json:"tool,omitempty"`
	// Args are tool-specific parameters. Ignored in chat mode.
	// +optional
	Args map[string]string `json:"args,omitempty"`
	// Prompt is a single-turn question. Ignored in chat mode.
	// +optional
	Prompt string `json:"prompt,omitempty"`
	// Messages is a multi-turn chat history. Ignored in chat mode (chat is
	// driven by HTTP requests to the runtime).
	// +optional
	Messages []AgentMessage `json:"messages,omitempty"`
}

// AgentMessage is one entry in a multi-turn chat history.
type AgentMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// AgentConfigSpec holds optional configuration values consumed by tools.
type AgentConfigSpec struct {
	// Namespace restricts the inspection scope to a single namespace.
	// When empty, cluster-wide resources are listed.
	// +optional
	Namespace string `json:"namespace,omitempty"`
}

// HistoryPersistenceSpec configures server-side chat history persistence.
//
// When enabled, the operator creates a PVC and mounts it to the runtime at
// /var/lib/agent-runtime/history. The runtime stores one JSON file per
// session id, so conversations survive pod restarts (including the
// credential-rotation rollout).
type HistoryPersistenceSpec struct {
	// Enabled turns persistence on. Defaults to false (stateless chat).
	// +optional
	Enabled bool `json:"enabled,omitempty"`
	// Size is the requested PVC capacity. Defaults to "1Gi" when empty.
	// +optional
	Size string `json:"size,omitempty"`
	// StorageClassName optionally pins a StorageClass. Empty means the
	// cluster default.
	// +optional
	StorageClassName *string `json:"storageClassName,omitempty"`
}

// AgentSpec defines the desired state of Agent.
type AgentSpec struct {
	Runtime AgentRuntimeSpec `json:"runtime"`
	Task    AgentTaskSpec    `json:"task,omitempty"`
	Config  AgentConfigSpec  `json:"config,omitempty"`
	// CredentialsSecret is the name of the Secret in the agent's namespace
	// that carries LLM_API_KEY / LLM_BASE_URL / LLM_MODEL. Its contents
	// are exposed to the runtime as envFrom. The operator also watches
	// this Secret and rolls the Deployment when its resourceVersion
	// changes (so a `kubectl edit secret` is enough to rotate creds).
	// +optional
	CredentialsSecret *string `json:"credentialsSecret,omitempty"`
	// WriteEnabled gates the destructive tools (restart_pod /
	// restart_deployment / scale_deployment). When true, the operator
	// binds the per-Agent ServiceAccount to the agent-runtime-write
	// ClusterRole. Defaults to false (read-only).
	// +optional
	WriteEnabled bool `json:"writeEnabled,omitempty"`
	// History turns on PVC-backed chat history persistence.
	// +optional
	History *HistoryPersistenceSpec `json:"history,omitempty"`
}

// AgentDeploymentRef references the Deployment that runs the agent.
type AgentDeploymentRef struct {
	Name string `json:"name,omitempty"`
}

// AgentServiceRef references the Service that fronts the agent.
type AgentServiceRef struct {
	Name string `json:"name,omitempty"`
}

// AgentJobRef is retained for backward compatibility with v0.1 Agent CRs.
// New reconciliations always use DeploymentRef / ServiceRef.
type AgentJobRef struct {
	Name string `json:"name,omitempty"`
}

// AgentCondition describes a condition the Agent has transitioned through.
type AgentCondition struct {
	Type               string      `json:"type"`
	Status             string      `json:"status"`
	LastTransitionTime metav1.Time `json:"lastTransitionTime"`
	Reason             string      `json:"reason,omitempty"`
	Message            string      `json:"message,omitempty"`
}

// AgentStatus defines the observed state of Agent.
type AgentStatus struct {
	// Phase reports the high-level execution state.
	// +optional
	Phase AgentPhase `json:"phase,omitempty"`
	// ObservedGeneration is the spec generation last processed by the
	// controller.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`
	// Conditions is a list of observed conditions.
	// +optional
	Conditions []AgentCondition `json:"conditions,omitempty"`
	// DeploymentRef is the Deployment running the agent.
	// +optional
	DeploymentRef AgentDeploymentRef `json:"deploymentRef,omitempty"`
	// ServiceRef is the Service fronting the agent.
	// +optional
	ServiceRef AgentServiceRef `json:"serviceRef,omitempty"`
	// ReadyReplicas is the number of pods ready to serve chat requests.
	// +optional
	ReadyReplicas int32 `json:"readyReplicas,omitempty"`
	// JobRef is retained for backward compatibility.
	// +optional
	JobRef AgentJobRef `json:"jobRef,omitempty"`
	// StartedAt is the time the Deployment was first observed ready.
	// +optional
	StartedAt *metav1.Time `json:"startedAt,omitempty"`
	// FinishedAt is unused in chat service mode.
	// +optional
	FinishedAt *metav1.Time `json:"finishedAt,omitempty"`
	// Result is unused in chat service mode.
	// +optional
	Result *runtime.RawExtension `json:"result,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`
// +kubebuilder:printcolumn:name="Generation",type=integer,JSONPath=`.status.observedGeneration`
// +kubebuilder:printcolumn:name="Ready",type=integer,JSONPath=`.status.readyReplicas`
// +kubebuilder:printcolumn:name="Deployment",type=string,JSONPath=`.status.deploymentRef.name`
// +kubebuilder:printcolumn:name="Service",type=string,JSONPath=`.status.serviceRef.name`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// Agent is the Schema for the agents API.
type Agent struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   AgentSpec   `json:"spec,omitempty"`
	Status AgentStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// AgentList contains a list of Agent.
type AgentList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Agent `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Agent{}, &AgentList{})
}
