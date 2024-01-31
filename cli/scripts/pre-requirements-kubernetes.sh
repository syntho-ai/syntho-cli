#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
source "$SCRIPT_DIR/utils.sh" --source-only

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
source "$SCRIPT_DIR/utils.sh" --source-only

source $DEPLOYMENT_DIR/.env --source-only
KUBECONFIG="$KUBECONFIG"


network_check() {
    sleep 2
    local errors=""

    check() {
        if ping -c 1 google.com &> /dev/null; then
            return 0  # Success, network connection exists
        else
            return 1  # Failure, no network connection
        fi
    }

    if ! check; then
        errors+="There is no active network connection.\n"
    fi

    write_and_exit "$errors" "network_check"
}

developer_tools_check() {
    sleep 2
    local errors=""

    # Check if curl or wget exists
    if ! command_exists "curl" && ! command_exists "wget"; then
        errors+="Missing command line tool - curl or wget\n"
    fi

    # Check if kubectl exists
    if ! command_exists "kubectlaaaaaaaa"; then
        errors+="Missing command line tool - kubectl\n"
    fi

    # Check if helm exists
    if ! command_exists "helm"; then
        errors+="Missing command line tool - helm\n"
    fi

    # Check if tar exists
    if ! command_exists "tar"; then
        errors+="Missing command line tool - tar\n"
    fi

    # Check if awk exists
    if ! command_exists "awk"; then
        errors+="Missing command line tool - awk\n"
    fi

    write_and_exit "$errors" "developer_tools_check"
}

kubernetes_cluster_check() {
    sleep 2
    local errors=""

    # Check if KUBECONFIG is set
    if [ -z "$KUBECONFIG" ]; then
        errors+="KUBECONFIG is not set.\n"
    fi

    # Check if KUBECONFIG points to a valid Kubernetes cluster
    if ! kubectl --kubeconfig="$KUBECONFIG" config current-context &> /dev/null; then
        errors+="KUBECONFIG does not point to a valid Kubernetes cluster.\n"
    fi

    write_and_exit "$errors" "kubernetes_cluster_check"
}


with_loading "Checking network connectivity" network_check
with_loading "Checking developer tools" developer_tools_check
with_loading "Checking if the given KUBECONFIG points to a valid k8s cluster" kubernetes_cluster_check
