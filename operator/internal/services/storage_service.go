// Package services contains the business-logic wrappers the controller
// calls. StorageService manages the per-Agent PVC used to persist chat
// history.
package services

import (
	"context"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"

	agentv1alpha1 "github.com/songli0103/kagent-ls/api/v1alpha1"
	"github.com/songli0103/kagent-ls/internal/deployment"
)

// StorageService manages the PVC for chat history persistence.
type StorageService struct {
	client client.Client
}

// NewStorageService constructs a StorageService.
func NewStorageService(c client.Client) *StorageService {
	return &StorageService{client: c}
}

// ReconcilePVC is the entry point: when spec.history.enabled is true, it
// ensures a PVC of the requested size exists. When false, it removes the
// PVC if it was previously created (so a "turn it off" spec change also
// cleans up).
//
// Idempotent.
func (s *StorageService) ReconcilePVC(ctx context.Context, agent *agentv1alpha1.Agent) error {
	if agent.Spec.History != nil && agent.Spec.History.Enabled {
		return s.ensurePVC(ctx, agent)
	}
	return s.deletePVC(ctx, agent)
}

// ensurePVC creates the PVC if missing. Returns nil if it already exists.
func (s *StorageService) ensurePVC(ctx context.Context, agent *agentv1alpha1.Agent) error {
	size := deployment.DefaultHistorySize
	sc := ""
	if agent.Spec.History != nil {
		if agent.Spec.History.Size != "" {
			size = agent.Spec.History.Size
		}
		if agent.Spec.History.StorageClassName != nil {
			sc = *agent.Spec.History.StorageClassName
		}
	}

	pvc := &corev1.PersistentVolumeClaim{
		ObjectMeta: metav1.ObjectMeta{
			Name:      deployment.PVCName(agent),
			Namespace: agent.Namespace,
			Labels:    deployment.LabelsFor(agent),
		},
		Spec: corev1.PersistentVolumeClaimSpec{
			AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
			Resources: corev1.VolumeResourceRequirements{
				Requests: corev1.ResourceList{
					corev1.ResourceStorage: resource.MustParse(size),
				},
			},
		},
	}
	if sc != "" {
		pvc.Spec.StorageClassName = &sc
	}

	if err := controllerutil.SetControllerReference(agent, pvc, s.client.Scheme()); err != nil {
		return fmt.Errorf("set controller reference on pvc: %w", err)
	}
	if err := s.client.Create(ctx, pvc); err != nil {
		if apierrors.IsAlreadyExists(err) {
			return nil
		}
		return fmt.Errorf("create pvc: %w", err)
	}
	return nil
}

// deletePVC removes the PVC if it exists. Tolerates NotFound.
func (s *StorageService) deletePVC(ctx context.Context, agent *agentv1alpha1.Agent) error {
	pvc := &corev1.PersistentVolumeClaim{
		ObjectMeta: metav1.ObjectMeta{
			Name:      deployment.PVCName(agent),
			Namespace: agent.Namespace,
		},
	}
	if err := s.client.Delete(ctx, pvc); err != nil {
		if apierrors.IsNotFound(err) {
			return nil
		}
		return err
	}
	return nil
}
