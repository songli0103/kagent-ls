// Package deployment contains the builder and label constants used by the
// Operator to materialise the Deployment + Service that runs a chat agent.
package deployment

// Shared label constants. The label on the Deployment, Pod template, and
// Service all match, so the Service selector finds the Pods and the
// operator can correlate the resources back to the owning Agent CR.
const (
	// AgentLabelKey groups all resources belonging to a single Agent.
	AgentLabelKey = "agent.demo.io/agent"
	// ComponentLabelKey distinguishes the runtime pods from any other pods
	// the user might add to the same namespace.
	ComponentLabelKey = "agent.demo.io/component"
	// ComponentValue is the value used for runtime pods/services.
	ComponentValue = "runtime"
	// ContainerName is the main container inside the runtime Pod.
	ContainerName = "agent"
	// Port is the container port the runtime server listens on.
	Port int32 = 8080
	// PortName is the named port used by the container and Service. Kept
	// in sync with the runtime image (which also listens under "http").
	PortName = "http"
)

// Resource defaults for the runtime container. Single source of truth so
// the Deployment builder, status reporting, and any docs all agree.
const (
	// DefaultCPURequest is the runtime's CPU request.
	DefaultCPURequest = "100m"
	// DefaultMemoryRequest is the runtime's memory request.
	DefaultMemoryRequest = "256Mi"
	// DefaultCPULimit is the runtime's CPU limit.
	DefaultCPULimit = "1"
	// DefaultMemoryLimit is the runtime's memory limit.
	DefaultMemoryLimit = "1Gi"
	// DefaultHistorySize is the PVC capacity requested for chat history
	// when spec.history.size is empty.
	DefaultHistorySize = "1Gi"
)

// Probe timings. Conservative defaults; the chat server usually reaches
// /ready within a couple of seconds but we give Kubernetes enough room.
const (
	// ReadinessInitialDelay is the seconds to wait before the first readiness probe.
	ReadinessInitialDelay = 5
	// ReadinessPeriod is the seconds between readiness probes.
	ReadinessPeriod = 10
	// LivenessInitialDelay is the seconds to wait before the first liveness probe.
	LivenessInitialDelay = 15
	// LivenessPeriod is the seconds between liveness probes.
	LivenessPeriod = 20
)
