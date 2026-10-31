import json
import base64
from pathlib import Path

import requests
from app.models.generation_policy import generation_settings


class OllamaAdapter:
    """Ollama /api/generate 어댑터.

    model을 생략하면 config/app_config.json의 ai.default_model을 사용한다.
    """

    def __init__(self, base_url=None, model=None, timeout=300, options=None, request_options=None):
        cfg = self._load_config()
        ai_cfg = cfg.get("ai", {})
        self.base_url = (base_url or ai_cfg.get("base_url", "http://localhost:11434")).rstrip("/")
        self.model = model or ai_cfg.get("default_model", "gemma4:12b")
        self.timeout = timeout
        default_options = ai_cfg.get("options", {
            "num_ctx": 8192,
            "num_predict": 4096,
        })
        self.options = options if options is not None else default_options
        self._base_options = dict(self.options)
        self._base_timeout = timeout
        self._ai_config = ai_cfg
        self._request_overrides = dict(request_options or {})
        self.last_response = {}
        self.configure_generation('work')

    def configure_generation(self, stage, attempt=0):
        self.options, self.timeout, self.request_options = generation_settings(
            self._ai_config, 'ollama', self.model, self._base_options,
            self._base_timeout, stage, attempt)
        self.request_options.update(self._request_overrides)

    @staticmethod
    def _load_config():
        try:
            path = Path(__file__).resolve().parents[2] / "config" / "app_config.json"
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "ai": {
                    "base_url": "http://localhost:11434",
                    "default_model": "gemma4:12b",
                    "options": {
                        "num_ctx": 8192,
                        "num_predict": 4096,
                    },
                }
            }

    def health(self):
        response = requests.get(f"{self.base_url}/api/tags", timeout=min(self.timeout, 10))
        response.raise_for_status()
        return response.json()

    def has_model(self):
        data = self.health()
        return any(m.get("name") == self.model for m in data.get("models", []))

    def generate_json(self, prompt, schema):
        return self.generate(prompt, response_schema=schema)

    def generate(self, prompt, images=None, response_schema=None):
        self.last_response = {}
        if not prompt or not str(prompt).strip():
            raise ValueError("Ollama prompt가 비어 있습니다.")

        payload = {
            "model": self.model,
            "prompt": str(prompt),
            "stream": False,
        }
        if self.options:
            payload["options"] = self.options
        for key in ('think', 'keep_alive'):
            if key in self.request_options:
                payload[key] = self.request_options[key]
        if images:
            encoded = []
            for image in images:
                path = Path(image)
                if path.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError(f"이미지 파일이 20MB를 초과합니다: {path.name}")
                encoded.append(base64.b64encode(path.read_bytes()).decode("ascii"))
            payload["images"] = encoded
        if response_schema is not None:
            payload["format"] = response_schema
        response = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError('Ollama 응답이 JSON 객체가 아닙니다.')
        self.last_response = {key: data.get(key) for key in (
            'model', 'done', 'done_reason', 'eval_count', 'eval_duration',
            'prompt_eval_count', 'total_duration')}
        self.last_response['truncated'] = data.get('done_reason') in {'length', 'max_tokens'} or data.get('done') is False
        text = data.get("response", "")
        if not isinstance(text, str):
            raise ValueError("Ollama 응답의 response 필드가 문자열이 아닙니다.")
        if not text.strip() and not self.last_response['truncated']:
            raise ValueError('Ollama가 빈 응답을 반환했습니다. 모델 응답 설정을 확인하세요.')
        return text
