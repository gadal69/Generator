"""Gemini adapter for public web research with Google Search grounding."""
from __future__ import annotations

import os
from pathlib import Path
import json


class GeminiSearchAdapter:
    def __init__(self, model=None, api_key=None):
        config_path = Path(__file__).resolve().parents[2] / "config" / "app_config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}
        self.model = model or config.get("gemini", {}).get("model", "gemini-3.8-flash")
        self.search_enabled = bool(config.get("gemini", {}).get("external_search_enabled", True))
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    @staticmethod
    def dependency_status():
        try:
            import importlib.util
            installed = importlib.util.find_spec("google.genai") is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            installed = False
        key_ready = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
        return {"sdk_installed": installed, "api_key_ready": key_ready}

    def search(self, prompt):
        if not self.search_enabled:
            raise RuntimeError("config/app_config.json에서 Gemini 외부 검색이 비활성화되어 있습니다.")
        if not self.api_key:
            raise RuntimeError(
                "Gemini 외부 검색을 위해 GEMINI_API_KEY 환경변수가 필요합니다. "
                "Google AI Studio에서 키를 발급한 뒤 Windows 환경변수에 등록하고 프로그램을 다시 시작하세요."
            )
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError(
                "Gemini SDK가 설치되어 있지 않습니다. 실행 전 `python -m pip install google-genai`를 실행하세요."
            ) from exc

        client = genai.Client(api_key=self.api_key)
        try:
            response = client.interactions.create(
                model=self.model,
                input=prompt,
                tools=[{"type": "google_search"}],
            )
        except Exception as exc:
            raise RuntimeError(f"Gemini Google 검색에 실패했습니다: {exc}") from exc
        finally:
            client.close()
        text = str(getattr(response, "output_text", "") or "").strip()
        if not text:
            raise RuntimeError("Gemini 검색 응답이 비어 있습니다. 검색 조건이나 모델 설정을 확인하세요.")
        sources = self._sources(response)
        if not sources:
            raise RuntimeError("Gemini 응답에 검증 가능한 검색 출처가 없습니다. 자료를 수집했다고 표시하지 않았습니다.")
        text += "\n\n검색 출처:\n" + "\n".join(
            f"- {item['title']}: {item['url']}" for item in sources
        )
        return {"text": text, "sources": sources, "model": self.model}

    def generate(self, prompt, images=None):
        if images:
            raise ValueError("Gemini 공개 검색 어댑터에는 이미지 전달을 지원하지 않습니다.")
        if not self.api_key:
            raise RuntimeError("Gemini execution requires GEMINI_API_KEY or GOOGLE_API_KEY.")
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("Install the Gemini SDK with: python -m pip install --upgrade google-genai") from exc
        client = genai.Client(api_key=self.api_key)
        try:
            response = client.interactions.create(
                model=self.model,
                input=str(prompt),
            )
        except Exception as exc:
            raise RuntimeError(f"Gemini task generation failed: {exc}") from exc
        finally:
            client.close()
        text = str(getattr(response, "output_text", "") or "").strip()
        if not text:
            raise RuntimeError("Gemini returned an empty task result.")
        return text

    @staticmethod
    def _sources(response):
        sources = []
        for step in getattr(response, "steps", None) or []:
            if str(getattr(step, "type", "")) != "model_output":
                continue
            for block in getattr(step, "content", None) or []:
                for annotation in getattr(block, "annotations", None) or []:
                    url = str(getattr(annotation, "url", "") or getattr(annotation, "uri", "") or "").strip()
                    title = str(getattr(annotation, "title", "") or url).strip()
                    if url and not any(item["url"] == url for item in sources):
                        sources.append({"title": title, "url": url})
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            metadata = getattr(candidate, "grounding_metadata", None)
            chunks = getattr(metadata, "grounding_chunks", None) or []
            for chunk in chunks:
                web = getattr(chunk, "web", None)
                url = str(getattr(web, "uri", "") or "").strip()
                title = str(getattr(web, "title", "") or url).strip()
                if url and not any(item["url"] == url for item in sources):
                    sources.append({"title": title, "url": url})
        return sources
