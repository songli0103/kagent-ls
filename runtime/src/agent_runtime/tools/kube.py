"""Helper that resolves the right Kubernetes API client based on whether
the runtime is running inside a cluster pod or on a developer machine.
"""
from __future__ import annotations

import logging
import os
from typing import Type, TypeVar

from kubernetes import config

logger = logging.getLogger(__name__)

T = TypeVar("T")

_initialised = False


def _initialise() -> None:
    global _initialised
    if _initialised:
        return
    if os.path.isdir("/var/run/secrets/kubernetes.io/serviceaccount"):
        logger.info("loading in-cluster Kubernetes configuration")
        config.load_incluster_config()
    else:
        logger.info("loading local kubeconfig (KUBECONFIG or ~/.kube/config)")
        config.load_kube_config()
    _initialised = True


def get_api(cls: Type[T]) -> T:
    """Return a configured Kubernetes API client of the requested class.

    A single factory for every K8s client the runtime needs — `CoreV1Api`,
    `AppsV1Api`, etc. Initialises the underlying config (in-cluster or
    kubeconfig) on the first call and reuses it thereafter, so call sites
    don't have to think about which API group they need.
    """
    _initialise()
    return cls()