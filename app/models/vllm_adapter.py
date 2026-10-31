import json
from pathlib import Path
import requests
from app.models.generation_policy import generation_settings


class VllmAdapter:
    """vLLM OpenAI 호환 API 어댑터.

    기본 엔드포인트: http://localhost:8000/v1
    """

    def __init__(self, base_url=None, model=None, timeout=300, options=None, request_options=None):
        cfg = self._load_config()
        ai_cfg = cfg.get("ai", {})
        vllm_cfg = ai_cfg.get("vllm", {})
        self.base_url = (base_url or vllm_cfg.get("base_url", "http://localhost:8000/v1")).rstrip("/")
        self.model = model or vllm_cfg.get("default_model") or ai_cfg.get("default_model", "gemma4:12b")
        self.timeout = timeout
        default_options = vllm_cfg.get("options", {
            "max_tokens": 4096,
            "temperature": 0.2,
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
            self._ai_config, 'vllm', self.model, self._base_options,
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
                    "vllm": {
                        "base_url": "http://localhost:8000/v1",
                        "default_model": "gemma4:12b",
                        "options": {"max_tokens": 4096, "temperature": 0.2},
                    }
                }
            }

    def health(self):
        response = requests.get(f"{self.base_url}/models", timeout=min(self.timeout, 10))
        response.raise_for_status()
        return response.json()

    def has_model(self):
        try:
            data = self.health()
            models = [m.get("id") for m in data.get("data", [])]
            return self.model in models or any(self.model in m for m in models)
        except Exception:
            return False

    def generate_json(self, prompt, schema):
        return self.generate(prompt, response_schema=schema)

    def generate(self, prompt, images=None, response_schema=None):
        self.last_response = {}
        if images:
            raise ValueError('현재 vLLM 어댑터는 이미지 입력을 지원하지 않습니다. 지원되는 엔진을 선택하세요.')
        if not prompt or not str(prompt).strip():
            raise ValueError("vLLM prompt가 비어 있습니다.")

        max_tokens = self.options.get("max_tokens") or self.options.get("num_predict") or 4096
        temperature = self.options.get("temperature", 0.2)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": str(prompt)}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        for key in ('top_p', 'stop', 'seed', 'presence_penalty', 'frequency_penalty'):
            if key in self.options:
                payload[key] = self.options[key]
        if 'chat_template_kwargs' in self.request_options:
            payload['chat_template_kwargs'] = self.request_options['chat_template_kwargs']

        if response_schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "verification_plan", "schema": response_schema}}
        response = requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError('vLLM 응답이 JSON 객체가 아닙니다.')
        choices = data.get("choices", [])
        if not choices:
            raise ValueError("vLLM 응답에 choices가 없습니다.")
        message = choices[0].get("message", {})
        reason = choices[0].get('finish_reason')
        self.last_response = {'model': data.get('model'), 'finish_reason': reason,
                              'usage': data.get('usage'), 'truncated': reason in {'length', 'max_tokens'}}
        text = message.get("content", "")
        if not isinstance(text, str):
            raise ValueError("vLLM 응답의 content 필드가 문자열이 아닙니다.")
        if not text.strip() and not self.last_response['truncated']:
            raise ValueError('vLLM이 빈 응답을 반환했습니다.')
        return text
