from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mistral_api_key: str | None = Field(default=None, alias="MISTRAL_API_KEY")
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    github_repo: str | None = Field(default=None, alias="GITHUB_REPO")
    prometheus_url: str = Field(
        default="http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090",
        alias="PROMETHEUS_URL",
    )
    chroma_persist_dir: str = Field(default="/data/chromadb", alias="CHROMA_PERSIST_DIR")
    memory_quality_threshold: float = Field(default=0.85, alias="MEMORY_QUALITY_THRESHOLD")
    memory_decay_rate: float = Field(default=0.02, alias="MEMORY_DECAY_RATE")
    simulation_confidence_auto_pr: float = Field(default=0.80, alias="SIMULATION_CONFIDENCE_AUTO_PR")
    simulation_confidence_low: float = Field(default=0.60, alias="SIMULATION_CONFIDENCE_LOW")
    kubeconfig: str = Field(default="/etc/rancher/k3s/k3s.yaml", alias="KUBECONFIG")
    default_namespace: str = Field(default="default", alias="DEFAULT_NAMESPACE")
    causal_window_minutes: int = Field(default=5, alias="CAUSAL_WINDOW_MINUTES")
    causal_interval_seconds: int = Field(default=60, alias="CAUSAL_INTERVAL_SECONDS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
