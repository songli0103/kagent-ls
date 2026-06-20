package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"regexp"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"
)

var resultBlockRegex = regexp.MustCompile(`(?s)__RESULT_BEGIN__\s*\n(.*)\n\s*__RESULT_END__`)

func main() {
	loadingRules := clientcmd.NewDefaultClientConfigLoadingRules()
	configOverrides := &clientcmd.ConfigOverrides{}
	kubeConfig := clientcmd.NewNonInteractiveDeferredLoadingClientConfig(loadingRules, configOverrides)
	cfg, err := kubeConfig.ClientConfig()
	if err != nil {
		panic(err)
	}
	cs, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		panic(err)
	}
	namespace := "default"
	jobName := "cluster-agent-job"

	// List pods with the agent's label
	pods, err := cs.CoreV1().Pods(namespace).List(context.Background(), metav1.ListOptions{
		LabelSelector: "agent.demo.io/job=" + jobName,
	})
	if err != nil {
		fmt.Println("list error:", err)
		os.Exit(1)
	}
	fmt.Printf("found %d pods\n", len(pods.Items))
	if len(pods.Items) == 0 {
		os.Exit(1)
	}
	for _, p := range pods.Items {
		fmt.Printf("  pod: %s (created: %s)\n", p.Name, p.CreationTimestamp)
	}
	pod := pods.Items[0]

	stream, err := cs.CoreV1().Pods(namespace).GetLogs(pod.Name, &corev1.PodLogOptions{
		Container: "agent",
	}).Stream(context.Background())
	if err != nil {
		fmt.Println("stream error:", err)
		os.Exit(1)
	}
	defer stream.Close()
	var buf bytes.Buffer
	io.Copy(&buf, stream)
	logs := buf.String()
	fmt.Printf("got %d bytes of logs\n", len(logs))

	matches := resultBlockRegex.FindStringSubmatch(logs)
	if len(matches) < 2 {
		fmt.Println("no match")
		os.Exit(1)
	}
	fmt.Printf("captured length: %d\n", len(matches[1]))
	var result map[string]interface{}
	if err := json.Unmarshal([]byte(matches[1]), &result); err != nil {
		fmt.Printf("decode error: %v\n", err)
		fmt.Printf("first 500 chars: %.500s\n", matches[1])
		os.Exit(1)
	}
	fmt.Printf("decoded OK, keys: %v\n", result)
}
