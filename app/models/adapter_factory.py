import json
from pathlib import Path
from app.models.ollama_adapter import OllamaAdapter
from app.models.vllm_adapter import VllmAdapter


def get_ai_config():
    try:
        path = Path(__file__).resolve().parents[2] / "config" / "app_config.json"
        return json.loads(path.read_text(encoding="utf-8")).get("ai", {})
    except Exception:
        return {}


def create_local_adapter(model=None, provider=None, **kwargs):
    """모델 접두사, provider 인자, 또는 app_config 설정에 따라 Ollama 또는 vLLM 어댑터를 반환합니다.

    예:
    - model='vllm:gemma-proposal' -> VllmAdapter(model='gemma-proposal')
    - model='ollama:gemma4:12b'   -> OllamaAdapter(model='gemma4:12b')
    - model='gemma4:12b'          -> app_config의 provider("ollama" 또는 "vllm")에 따라 자동 결정
    """
    ai_cfg = get_ai_config()
    target_model = model

    if target_model and str(target_model).startswith("vllm:"):
        provider = "vllm"
        target_model = target_model.split(":", 1)[1]
    elif target_model and str(target_model).startswith("ollama:"):
        provider = "ollama"
        target_model = target_model.split(":", 1)[1]

    if provider is None:
        provider = ai_cfg.get("provider", "ollama")

    if str(provider).lower() == "vllm":
        return VllmAdapter(model=target_model, **kwargs)
    return OllamaAdapter(model=target_model, **kwargs)
