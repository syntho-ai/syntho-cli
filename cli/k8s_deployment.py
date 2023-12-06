import os
import shutil
import subprocess
import yaml
import click

from typing import Dict, NoReturn, List
from hashlib import md5
from functools import wraps
from datetime import datetime
from enum import Enum
from collections import namedtuple


DeploymentResult = namedtuple("DeploymentResult", [
    "succeeded",
    "deployment_id",
    "error",
    "deployment_status",
])


SubprocessResult = namedtuple("SubprocessResult", [
    "succeeded",
    "output",
    "exitcode",
])


class CleanUpLevel(Enum):
    FULL = "full"
    DIR = "dir"
    NA = "not-applicable"


class DeploymentStatus(Enum):
    INITIALIZING = ("initializing", CleanUpLevel.DIR)
    PREPARING_ENV = ("preparing-env", CleanUpLevel.DIR)
    INITIALIZED = ("initialized", CleanUpLevel.DIR)
    PRE_REQ_CHECK_IN_PROGRESS = ("pre-req-check-in-progress", CleanUpLevel.DIR)
    PRE_REQ_CHECK_SUCCEEDED = ("pre-req-check-succeeded", CleanUpLevel.DIR)
    PRE_REQ_CHECK_FAILED = ("pre-req-check-failed", CleanUpLevel.DIR)
    PRE_DEPLOYMENT_OPERATIONS_IN_PROGRESS = ("pre-deployment-operations-in-progress",
                                             CleanUpLevel.FULL)
    PRE_DEPLOYMENT_OPERATIONS_SUCCEEDED = ("pre-deployment-operations-succeeded",
                                           CleanUpLevel.FULL)
    PRE_DEPLOYMENT_OPERATIONS_FAILED = ("pre-deployment-operations-failed", CleanUpLevel.FULL)
    RAY_CLUSTER_DEPLOYMENT_IN_PROGRESS = ("ray-cluster-deployment-in-progress", CleanUpLevel.FULL)
    RAY_CLUSTER_DEPLOYMENT_SUCCEEDED = ("ray-cluster-deployment-succeeded", CleanUpLevel.FULL)
    RAY_CLUSTER_DEPLOYMENT_FAILED = ("ray-cluster-deployment-failed", CleanUpLevel.FULL)
    SYNTHO_UI_DEPLOYMENT_IN_PROGRESS = ("syntho-ui-deployment-in-progress", CleanUpLevel.FULL)
    SYNTHO_UI_DEPLOYMENT_SUCCEEDED = ("syntho-ui-deployment-succeeded", CleanUpLevel.FULL)
    SYNTHO_UI_DEPLOYMENT_FAILED = ("syntho-ui-deployment-failed", CleanUpLevel.FULL)
    COMPLETED = ("completed", CleanUpLevel.NA)

    def get(self):
        return self.value[0]

    def cleanup_level(self):
        return self.value[1]


def with_working_directory(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        original_dir = os.getcwd()

        try:
            result = func(*args, **kwargs)

            return result

        finally:
            os.chdir(original_dir)

    return wrapper


@with_working_directory
def start(scripts_dir: str,
          license_key: str,
          registry_user: str,
          registry_pwd: str,
          kubeconfig: str,
          arch_value: str,
          version: str) -> str:

    deployments_dir = get_deployments_dir(scripts_dir)
    deployment_id = generate_deployment_id(kubeconfig)
    deployment_dir = f"{deployments_dir}/{deployment_id}"

    deployment_status = get_deployment_status(deployments_dir, deployment_dir, deployment_id)
    if deployment_status:
        if deployment_status.get() == DeploymentStatus.COMPLETED.get():
            return DeploymentResult(
                succeeded=True,
                deployment_id=deployment_id,
                error=None,
                deployment_status=deployment_status,
            )
        else:
            return DeploymentResult(
                succeeded=False,
                deployment_id=deployment_id,
                error=("Deployment remained unfinished"),
                deployment_status=deployment_status,
            )

    click.echo("--Syntho stack is going to be deployed--")

    initialize_deployment(deployment_id, deployment_dir, deployments_dir, version)

    prepare_env(
        deployment_id,
        deployment_dir,
        deployments_dir,
        license_key,
        registry_user,
        registry_pwd,
        arch_value,
        kubeconfig,
        version,
    )

    succeeded = pre_requirements_check(scripts_dir, deployment_id)
    if not succeeded:
        return DeploymentResult(
            succeeded=False,
            deployment_id=deployment_id,
            error=("Pre requirements check failed"),
            deployment_status=DeploymentStatus.PRE_REQ_CHECK_FAILED,
        )

    set_cluster_name(scripts_dir, deployment_id)

    succeeded = configuration_questions(scripts_dir, deployment_id)
    if not succeeded:
        return DeploymentResult(
            succeeded=False,
            deployment_id=deployment_id,
            error=("Pre deployment operations failed - Configuration"),
            deployment_status=DeploymentStatus.PRE_DEPLOYMENT_OPERATIONS_FAILED,
        )

    succeeded = download_syntho_charts_release(scripts_dir, deployment_id)
    if not succeeded:
        return DeploymentResult(
            succeeded=False,
            deployment_id=deployment_id,
            error=("Pre deployment operations failed - Downloading syntho-charts release"),
            deployment_status=DeploymentStatus.PRE_DEPLOYMENT_OPERATIONS_FAILED,
        )

    succeeded = major_predeployment_operations(scripts_dir, deployment_id)
    if not succeeded:
        return DeploymentResult(
            succeeded=False,
            deployment_id=deployment_id,
            error=("Pre deployment operations failed - Setting up major pre-deployment components"),
            deployment_status=DeploymentStatus.PRE_DEPLOYMENT_OPERATIONS_FAILED,
        )

    succeeded = start_deployment(scripts_dir, deployment_id)
    if not succeeded:
        return DeploymentResult(
            succeeded=False,
            deployment_id=deployment_id,
            error=("Syntho UI deployment failed"),
            deployment_status=DeploymentStatus.SYNTHO_UI_DEPLOYMENT_FAILED,
        )

    set_state(deployment_id, deployments_dir, DeploymentStatus.COMPLETED, is_completed=True)
    return DeploymentResult(
        succeeded=True,
        deployment_id=deployment_id,
        error=None,
        deployment_status=DeploymentStatus.COMPLETED,
    )

def is_deployment_completed(deployments_dir: str, deployment_id: str) -> bool:
    deployment_state_path = f"{deployments_dir}/k8s-deployment-state.yaml"
    deployment_state_dict = None
    with open(deployment_state_path, "r") as file:
        deployment_state_dict = yaml.safe_load(file)

    deployment = next(
        filter(lambda d: d["id"] == deployment_id, deployment_state_dict["deployments"]), None
    )
    return deployment["status"] == "completed"


def run_script(scripts_dir:str,
               deployment_dir: str,
               script_name: str,
               capture_output: bool = False) -> SubprocessResult:
    env = {
        "DEPLOYMENT_DIR": deployment_dir,
        "PATH": os.environ.get("PATH", ""),
    }
    script_path = os.path.join(scripts_dir, script_name)

    try:
        if capture_output:
            res = subprocess.run([script_path], check=True, shell=False, env=env,
                                 capture_output=True, text=True)
        else:
            res = subprocess.run([script_path], check=True, shell=False, env=env)

        return SubprocessResult(
            succeeded=True,
            output=res.stdout.strip() if res.stdout else "",
            exitcode=res.returncode,
        )
    except subprocess.CalledProcessError as e:
        return SubprocessResult(succeeded=False, output=e.stderr, exitcode=e.returncode)
    except KeyboardInterrupt:
        return SubprocessResult(succeeded=False, output="", exitcode=1)
    except Exception as e:
        return SubprocessResult(succeeded=False, output="", exitcode=1)



def cleanup(scripts_dir: str, deployment_id: str, status: DeploymentStatus):
    cleanup_with_cleanup_level(scripts_dir, deployment_id, status.cleanup_level())


def cleanup_with_cleanup_level(scripts_dir: str, deployment_id: str, cleanup_level: CleanUpLevel) -> bool:
    if cleanup_level == CleanUpLevel.NA:
        return

    deployments_dir = f"{scripts_dir}/deployments"
    deployment_dir = f"{deployments_dir}/{deployment_id}"

    if not os.path.isdir(deployment_dir):
        return

    if cleanup_level == CleanUpLevel.FULL:
        os.chdir(deployment_dir)
        result = run_script(scripts_dir, deployment_dir, "cleanup-kubernetes.sh")
        if not result.succeeded:
            return False

    shutil.rmtree(deployment_dir)

    deployments_state = get_deployments_state(deployments_dir)
    deployments_state["active_deployment_id"] = None

    deployments = deployments_state["deployments"]
    _deployments = []
    for deployment in deployments:
        if deployment["id"] == deployment_id:
            continue

        _deployments.append(deployment)

    deployments_state["deployments"] = _deployments
    if _deployments:
        deployments_state["active_deployment_id"] = _deployments[-1]["id"]
    update_deployments_state(deployments_dir, deployments_state)
    return True


@with_working_directory
def destroy(scripts_dir: str, deployment_id: str):
    deployments_dir = f"{scripts_dir}/deployments"
    deployment_dir = f"{deployments_dir}/{deployment_id}"
    if not os.path.isdir(deployment_dir):
        click.echo(f"Deployment({deployment_id}) couldn't be found")
        return

    click.echo(f"Deployment({deployment_id}) will be destroyed alongside its components")
    cleanup_with_cleanup_level(scripts_dir, deployment_id, CleanUpLevel.FULL)
    click.echo(
        f"Deployment({deployment_id}) is destroyed and all its components have been removed"
    )


def generate_deployment_id(kubeconfig: str) -> str:
    kubeconfig_content = kubeconfig
    if os.path.isfile(kubeconfig):
        with open(file_path, "r") as file:
            kubeconfig_content = file.read()

    return md5(kubeconfig_content.encode()).hexdigest()


def get_deployments_dir(scripts_dir: str) -> str:
    deployments_dir = f"{scripts_dir}/deployments" 
    if not os.path.exists(deployments_dir):
        os.makedirs(deployments_dir)

    return deployments_dir


def get_deployment_status(deployments_dir: str,
                          deployment_dir: str,
                          deployment_id: str) -> DeploymentStatus:
    exists = os.path.exists(deployment_dir)
    if not exists:
        return None

    deployments_state = get_deployments_state(deployments_dir)
    filtered = list(filter(lambda d: d["id"] == deployment_id, deployments_state["deployments"]))
    deployment = filtered[0]

    for deployment_status in DeploymentStatus:
        if deployment_status.get() == deployment["status"]:
            return deployment_status


def get_deployments_state(deployments_dir: str) -> Dict:
    deployment_state_path = f"{deployments_dir}/k8s-deployment-state.yaml"
    if os.path.exists(deployment_state_path):
        with open(deployment_state_path, "r") as file:
            deployment_state_dict = yaml.safe_load(file)
            return deployment_state_dict

    return {
        "active_deployment_id": None,
        "deployments": [],
    }


def initialize_deployment(deployment_id: str,
                          deployment_dir: str,
                          deployments_dir: str,
                          version: str) -> NoReturn:

    deployments_state = get_deployments_state(deployments_dir)

    started_at = datetime.utcnow().isoformat()
    os.makedirs(deployment_dir)

    os.chdir(deployment_dir)

    deployment = {
        "id": deployment_id,
        "status": DeploymentStatus.INITIALIZING.get(),
        "version": version,
        "started_at": started_at,
        "finished_at": None,
        "cluster_name": None,
    }
    deployments_state["deployments"].append(deployment)
    deployments_state["active_deployment_id"] = deployment_id

    update_deployments_state(deployments_dir, deployments_state)


def update_deployments_state(deployments_dir: str, deployments_state: Dict) -> NoReturn:
    deployment_state_path = f"{deployments_dir}/k8s-deployment-state.yaml"
    with open(deployment_state_path, "w") as file:
        yaml.dump(deployments_state, file, default_flow_style=False)


def prepare_env(deployment_id: str,
                deployment_dir: str,
                deployments_dir: str,
                license_key: str,
                registry_user: str,
                registry_pwd: str,
                arch_value: str,
                kubeconfig: str,
                version: str):

    set_state(deployment_id, deployments_dir, DeploymentStatus.PREPARING_ENV)


    kube_dir = f"{deployment_dir}/.kube"
    if not os.path.exists(kube_dir):
        os.makedirs(kube_dir)

    if os.path.isfile(kubeconfig):
        os.symlink(kubeconfig, f"{kube_dir}/config")
    else:
        with open(f"{kube_dir}/config", "w") as kubeconfig_file:
            kubeconfig_file.write(kubeconfig)

    kubeconfig_file_path = f"{kube_dir}/config"

    env = {
        "LICENSE_KEY": license_key,
        "REGISTRY_USER": registry_user,
        "REGISTRY_PWD": registry_pwd,
        "ARCH": arch_value,
        "KUBECONFIG": kubeconfig_file_path,
        "VERSION": version
    }
    env_file_path = f"{deployment_dir}/.env"
    with open(env_file_path, "w") as file:
        for key, value in env.items():
            file.write(f"{key}={value}\n")

    set_state(deployment_id, deployments_dir, DeploymentStatus.INITIALIZED)


def set_state(deployment_id: str,
              deployments_dir: str,
              status: DeploymentStatus,
              is_completed: bool = False):
    deployments_state = get_deployments_state(deployments_dir)
    for deployment in deployments_state["deployments"]:
        if deployment["id"] == deployment_id:
            deployment["status"] = status.get()
            if is_completed:
                finished_at = datetime.utcnow().isoformat()
                deployment["finished_at"] = finished_at

    update_deployments_state(deployments_dir, deployments_state)


def pre_requirements_check(scripts_dir: str, deployment_id: str) -> bool:
    click.echo("Step 1: Pre-requirement check;")

    deployments_dir = f"{scripts_dir}/deployments"
    deployment_dir = f"{deployments_dir}/{deployment_id}"
    set_state(deployment_id, deployments_dir, DeploymentStatus.PRE_REQ_CHECK_IN_PROGRESS)

    result = run_script(scripts_dir, deployment_dir, "pre-requirements-kubernetes.sh")
    if result.succeeded:
        set_state(deployment_id, deployments_dir, DeploymentStatus.PRE_REQ_CHECK_SUCCEEDED)
    else:
        set_state(deployment_id, deployments_dir, DeploymentStatus.PRE_REQ_CHECK_FAILED)

    return result.succeeded


def get_active_deployment_id(scripts_dir: str) -> str:
    deployments_dir = f"{scripts_dir}/deployments"
    deployments_state = get_deployments_state(deployments_dir)
    return deployments_state["active_deployment_id"]


def get_deployment(scripts_dir: str, deployment_id: str) -> Dict:
    deployments_dir = f"{scripts_dir}/deployments"
    deployments_state = get_deployments_state(deployments_dir)
    deployments = deployments_state["deployments"]
    for deployment in deployments:
        if deployment["id"] == deployment_id:
            return deployment


def get_deployments(scripts_dir: str) -> List[Dict]:
    deployments_dir = f"{scripts_dir}/deployments"
    deployments_state = get_deployments_state(deployments_dir)
    return deployments_state["deployments"]


def set_cluster_name(scripts_dir: str, deployment_id: str) -> NoReturn:
    deployments_dir = f"{scripts_dir}/deployments"
    deployment_dir = f"{deployments_dir}/{deployment_id}"
    result = run_script(
        scripts_dir, deployment_dir, "get-k8s-cluster-context-name.sh", capture_output=True
    )
    cluster_name = result.output

    deployments_state = get_deployments_state(deployments_dir)
    for deployment in deployments_state["deployments"]:
        if deployment["id"] == deployment_id:
            deployment["cluster_name"] = cluster_name

    update_deployments_state(deployments_dir, deployments_state)

def start_deployment(scripts_dir: str, deployment_id: str) -> bool:
    click.echo("Step 5: Deployment;")
    deployments_dir = f"{scripts_dir}/deployments"
    deployment_dir = f"{deployments_dir}/{deployment_id}"
    set_state(deployment_id, deployments_dir, DeploymentStatus.SYNTHO_UI_DEPLOYMENT_IN_PROGRESS)

    result = run_script(scripts_dir, deployment_dir, "deploy-ray-and-syntho-stack.sh")
    if result.succeeded:
        set_state(deployment_id, deployments_dir, DeploymentStatus.SYNTHO_UI_DEPLOYMENT_SUCCEEDED)
    else:
        set_state(deployment_id, deployments_dir, DeploymentStatus.SYNTHO_UI_DEPLOYMENT_FAILED)

    return result.succeeded


def configuration_questions(scripts_dir: str, deployment_id: str) -> bool:
    click.echo("Step 2: Configuration;")
    deployments_dir = f"{scripts_dir}/deployments"
    deployment_dir = f"{deployments_dir}/{deployment_id}"
    set_state(deployment_id, deployments_dir, DeploymentStatus.PRE_DEPLOYMENT_OPERATIONS_IN_PROGRESS)

    result = run_script(scripts_dir, deployment_dir, "configuration-questions.sh")
    if not result.succeeded:
        set_state(deployment_id, deployments_dir, DeploymentStatus.PRE_DEPLOYMENT_OPERATIONS_FAILED)

    return result.succeeded


def download_syntho_charts_release(scripts_dir: str, deployment_id: str) -> bool:
    click.echo("Step 3: Downloading the release;")
    deployments_dir = f"{scripts_dir}/deployments"
    deployment_dir = f"{deployments_dir}/{deployment_id}"
    set_state(deployment_id, deployments_dir, DeploymentStatus.PRE_DEPLOYMENT_OPERATIONS_IN_PROGRESS)

    result = run_script(scripts_dir, deployment_dir, "download-syntho-charts-release.sh")
    if not result.succeeded:
        set_state(deployment_id, deployments_dir, DeploymentStatus.PRE_DEPLOYMENT_OPERATIONS_FAILED)

    return result.succeeded


def major_predeployment_operations(scripts_dir: str, deployment_id: str) -> bool:
    click.echo("Step 4: Major pre-deployment operations;")
    deployments_dir = f"{scripts_dir}/deployments"
    deployment_dir = f"{deployments_dir}/{deployment_id}"
    set_state(deployment_id, deployments_dir, DeploymentStatus.PRE_DEPLOYMENT_OPERATIONS_IN_PROGRESS)

    result = run_script(scripts_dir, deployment_dir, "major-pre-deployment-operations.sh")
    if not result.succeeded:
        set_state(deployment_id, deployments_dir, DeploymentStatus.PRE_DEPLOYMENT_OPERATIONS_FAILED)

    return result.succeeded
