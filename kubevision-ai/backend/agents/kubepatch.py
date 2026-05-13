from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import yaml
from github import Github
from kubernetes import client, config
from kubernetes.client import ApiClient
from kubernetes.client.exceptions import ApiException
from mistralai import Mistral

from config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KubePatchResult:
    incident_id: str
    action: str
    confidence: float
    current_yaml: str | None
    generated_yaml: str | None
    recommendation: str
    pr_url: str | None = None
    pr_number: int | None = None
    branch: str | None = None
    file_path: str | None = None
    label: str | None = None
    yaml_diff: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "action": self.action,
            "confidence": self.confidence,
            "current_yaml": self.current_yaml,
            "generated_yaml": self.generated_yaml,
            "recommendation": self.recommendation,
            "pr_url": self.pr_url,
            "pr_number": self.pr_number,
            "branch": self.branch,
            "file_path": self.file_path,
            "label": self.label,
            "yaml_diff": self.yaml_diff,
        }


class KubePatchAgent:
    """Generate Kubernetes YAML fixes and open approval-ready GitHub PRs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._kubernetes_loaded = False

    def _load_kubernetes_config(self) -> None:
        if self._kubernetes_loaded:
            return
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config(config_file=self.settings.kubeconfig)
        self._kubernetes_loaded = True

    def _kubectl_get_yaml(self, pod_name: str, namespace: str) -> str:
        self._load_kubernetes_config()
        core = client.CoreV1Api()
        apps = client.AppsV1Api()
        api_client = ApiClient()

        try:
            pod = core.read_namespaced_pod(name=pod_name, namespace=namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
            deployment = apps.read_namespaced_deployment(name=pod_name, namespace=namespace)
            sanitized_deployment = api_client.sanitize_for_serialization(deployment)
            self._strip_runtime_fields(sanitized_deployment)
            return yaml.safe_dump(sanitized_deployment, sort_keys=False)

        workload: Any = pod

        for owner in pod.metadata.owner_references or []:
            if owner.kind == "ReplicaSet":
                replica_set = apps.read_namespaced_replica_set(name=owner.name, namespace=namespace)
                workload = replica_set
                for rs_owner in replica_set.metadata.owner_references or []:
                    if rs_owner.kind == "Deployment":
                        workload = apps.read_namespaced_deployment(name=rs_owner.name, namespace=namespace)
                        break
                break
            if owner.kind == "Deployment":
                workload = apps.read_namespaced_deployment(name=owner.name, namespace=namespace)
                break
            if owner.kind == "StatefulSet":
                workload = apps.read_namespaced_stateful_set(name=owner.name, namespace=namespace)
                break
            if owner.kind == "DaemonSet":
                workload = apps.read_namespaced_daemon_set(name=owner.name, namespace=namespace)
                break

        sanitized = api_client.sanitize_for_serialization(workload)
        self._strip_runtime_fields(sanitized)
        return yaml.safe_dump(sanitized, sort_keys=False)

    def _generate_yaml_patch(self, current_yaml: str, proposed_changes: dict[str, Any]) -> str:
        if not self.settings.mistral_api_key:
            raise RuntimeError("MISTRAL_API_KEY is required for YAML generation")

        prompt = self._build_yaml_prompt(current_yaml, proposed_changes)
        mistral = Mistral(api_key=self.settings.mistral_api_key)
        response = mistral.chat.complete(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.1,
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Mistral returned an empty YAML patch")

        generated_yaml = self._extract_yaml(content)
        self._validate_yaml(generated_yaml)
        return generated_yaml

    async def generate_and_pr(
        self,
        incident: dict[str, Any],
        supervisor_recommendation: dict[str, Any],
        force_pr: bool = False,
    ) -> KubePatchResult:
        incident_id = incident["id"]
        pod_name = incident["affected_pod"]
        namespace = incident["namespace"]
        proposed_changes = supervisor_recommendation["proposed_changes"]
        confidence = float(supervisor_recommendation["confidence"])

        current_yaml = await asyncio.to_thread(self._kubectl_get_yaml, pod_name, namespace)
        generated_yaml = await asyncio.to_thread(self._generate_yaml_patch, current_yaml, proposed_changes)
        yaml_diff = self._build_yaml_diff(current_yaml, generated_yaml)

        if confidence < self.settings.simulation_confidence_low:
            return KubePatchResult(
                incident_id=incident_id,
                action="manual_review_required",
                confidence=confidence,
                current_yaml=current_yaml,
                generated_yaml=generated_yaml,
                recommendation="LOW CONFIDENCE - Manual review required.",
                label="LOW CONFIDENCE - Manual review required",
                yaml_diff=yaml_diff,
            )

        if confidence < self.settings.simulation_confidence_auto_pr and not force_pr:
            return KubePatchResult(
                incident_id=incident_id,
                action="manual_pr_required",
                confidence=confidence,
                current_yaml=current_yaml,
                generated_yaml=generated_yaml,
                recommendation="Generated YAML patch requires manual Generate PR approval.",
                yaml_diff=yaml_diff,
            )

        await asyncio.sleep(1)
        pr_description = await asyncio.to_thread(self._generate_pr_description, incident, supervisor_recommendation)
        pr_details = await asyncio.to_thread(
            self._open_github_pr,
            incident,
            supervisor_recommendation,
            generated_yaml,
            pr_description,
        )
        return KubePatchResult(
            incident_id=incident_id,
            action="pr_opened",
            confidence=confidence,
            current_yaml=current_yaml,
            generated_yaml=generated_yaml,
            recommendation="Opened GitHub PR for human review.",
            pr_url=pr_details["pr_url"],
            pr_number=pr_details["pr_number"],
            branch=pr_details["branch"],
            file_path=pr_details["file_path"],
            yaml_diff=yaml_diff,
        )

    def approve_pr(self, pr_number: int) -> str:
        repo = self._github_repo()
        pr = repo.get_pull(pr_number)
        review = pr.create_review(event="APPROVE")
        return review.html_url

    def _open_github_pr(
        self,
        incident: dict[str, Any],
        supervisor_recommendation: dict[str, Any],
        generated_yaml: str,
        pr_description: str,
    ) -> dict[str, Any]:
        repo = self._github_repo()
        base_branch = "main"
        base = repo.get_branch(base_branch)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        branch = f'ai-fix/{incident["affected_pod"]}-{timestamp}'

        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base.commit.sha)

        file_path = f'manifests/{incident["namespace"]}/{incident["affected_pod"]}.yaml'
        commit_message = f'AI Fix: {incident["root_cause"]}'

        repo.create_file(
            path=file_path,
            message=commit_message,
            content=generated_yaml,
            branch=branch,
        )

        pr = repo.create_pull(
            title=f'[KubeVision AI] Fix: {incident["root_cause"]}',
            body=pr_description,
            head=branch,
            base=base_branch,
        )
        return {"pr_url": pr.html_url, "pr_number": pr.number, "branch": branch, "file_path": file_path}

    def _github_repo(self):
        if not self.settings.github_token:
            raise RuntimeError("GITHUB_TOKEN is required for PR creation")
        if not self.settings.github_repo:
            raise RuntimeError("GITHUB_REPO is required for PR creation")
        github = Github(self.settings.github_token)
        return github.get_repo(self.settings.github_repo)

    @staticmethod
    def _build_yaml_prompt(current_yaml: str, proposed_changes: dict[str, Any]) -> str:
        return f"""
You are a Kubernetes YAML specialist.
Below is the CURRENT deployment manifest. Apply ONLY the specified change.
Output ONLY valid YAML. No explanations, no markdown, no preamble.

CURRENT YAML:
{current_yaml}

CHANGE REQUIRED:
{json.dumps(proposed_changes, indent=2)}

Output the complete corrected YAML:
"""

    def _generate_pr_description(self, incident: dict[str, Any], supervisor_recommendation: dict[str, Any]) -> str:
        if not self.settings.mistral_api_key:
            raise RuntimeError("MISTRAL_API_KEY is required for PR description generation")

        prompt = (
            "Write a concise GitHub pull request description for a Kubernetes remediation.\n"
            "Ground the description only in the incident and recommendation JSON.\n\n"
            f"INCIDENT:\n{json.dumps(incident, indent=2, default=str)}\n\n"
            f"SUPERVISOR_RECOMMENDATION:\n{json.dumps(supervisor_recommendation, indent=2, default=str)}"
        )
        mistral = Mistral(api_key=self.settings.mistral_api_key)
        response = mistral.chat.complete(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.1,
        )
        content = response.choices[0].message.content
        if isinstance(content, str) and content.strip():
            return content.strip()
        return f'KubeVision AI remediation for {incident["namespace"]}/{incident["affected_pod"]}.'

    @staticmethod
    def _build_yaml_diff(current_yaml: str, generated_yaml: str) -> str:
        return "".join(
            difflib.unified_diff(
                current_yaml.splitlines(keepends=True),
                generated_yaml.splitlines(keepends=True),
                fromfile="current.yaml",
                tofile="corrected.yaml",
            )
        )

    @staticmethod
    def _extract_yaml(content: str) -> str:
        stripped = content.strip()
        fence_match = re.search(r"```(?:yaml|yml)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
        if fence_match:
            stripped = fence_match.group(1).strip()
        return stripped

    @staticmethod
    def _validate_yaml(generated_yaml: str) -> None:
        documents = [document for document in yaml.safe_load_all(generated_yaml) if document]
        if not documents:
            raise RuntimeError("Generated patch does not contain a YAML document")
        for document in documents:
            if not isinstance(document, dict):
                raise RuntimeError("Generated YAML document must be a mapping")
            for key in ("apiVersion", "kind", "metadata"):
                if key not in document:
                    raise RuntimeError(f"Generated YAML is missing required key: {key}")

    @staticmethod
    def _strip_runtime_fields(manifest: dict[str, Any]) -> None:
        metadata = manifest.get("metadata", {})
        for key in (
            "annotations",
            "creationTimestamp",
            "generation",
            "managedFields",
            "resourceVersion",
            "selfLink",
            "uid",
        ):
            metadata.pop(key, None)
        manifest.pop("status", None)
        spec = manifest.get("spec", {})
        template_metadata = spec.get("template", {}).get("metadata", {})
        template_metadata.pop("creationTimestamp", None)
