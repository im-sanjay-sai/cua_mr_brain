from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


DEFAULT_OPENAI_MODEL = "gpt-5.5"
DEFAULT_TERM_LIMIT = 30
DEFAULT_TZAFON_MODEL = "tzafon.northstar-cua-fast"
DEFAULT_TZAFON_BASE_URL = "https://api.tzafon.ai/v1"

ReportProvider = Literal["openai", "lightcone"]


@dataclass
class ReportTerm:
    name: str
    query: str = ""
    aliases: list[str] = field(default_factory=list)
    context: str = ""
    source_sentence: str = ""
    enabled: bool = True

    @property
    def prompt_query(self) -> str:
        return self.query.strip() or self.name.strip()


@dataclass
class TermExtractionResult:
    provider: str
    terms: list[ReportTerm]
    raw: str = ""


def extract_terms_from_report(
    report_text: str,
    *,
    provider: ReportProvider = "openai",
    openai_api_key: str | None = None,
    openai_model: str | None = None,
    tzafon_api_key: str | None = None,
    tzafon_base_url: str | None = None,
    tzafon_model: str | None = None,
) -> TermExtractionResult:
    report = report_text.strip()
    if not report:
        raise ValueError("Report text is empty.")

    if provider == "lightcone":
        return _extract_terms_lightcone(
            report,
            api_key=tzafon_api_key,
            base_url=tzafon_base_url,
            model=tzafon_model,
        )
    return _extract_terms_openai(report, api_key=openai_api_key, model=openai_model)


def build_localization_prompt(term: ReportTerm) -> str:
    parts = [
        f'Report-derived target term: "{term.prompt_query}".',
        "Find this exact visible structure, finding, or region in the medical image.",
        "Mark it only when it is visibly present in this image.",
        "If it is absent, not shown in this slice/view, or not reliably localizable, return no_region_found.",
        "Do not infer a location from the report alone.",
    ]
    aliases = [alias.strip() for alias in term.aliases if alias.strip()]
    if aliases:
        parts.append("Useful aliases/context words: " + ", ".join(aliases[:8]) + ".")
    if term.context.strip():
        parts.append("Report context: " + term.context.strip())
    return "\n".join(parts)


def load_secret_from_env_or_shell_rc(name: str, override: str | None = None) -> str | None:
    if override and override.strip():
        return override.strip()
    value = os.getenv(name)
    if value:
        return value.strip()
    return _load_shell_rc_value(name)


def _extract_terms_openai(
    report_text: str,
    *,
    api_key: str | None,
    model: str | None,
) -> TermExtractionResult:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install dependencies first: python -m pip install -r requirements.txt") from exc

    resolved_key = load_secret_from_env_or_shell_rc("OPENAI_API_KEY", api_key)
    if not resolved_key:
        raise RuntimeError("Set OPENAI_API_KEY in the environment, ~/.bashrc, ~/.zshrc, or paste an OpenAI key.")

    client = OpenAI(api_key=resolved_key)
    if not hasattr(client, "responses"):
        raise RuntimeError("The installed openai package is too old for the Responses API. Upgrade requirements.")

    response = client.responses.create(
        model=model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        store=False,
        reasoning={"effort": "low"},
        instructions=_term_extraction_instructions(),
        input=report_text,
        text={
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "report_terms",
                "strict": True,
                "schema": _terms_schema(),
            },
        },
    )
    raw = _dump_response(response)
    payload = _parse_json_text(_response_text(response))
    return TermExtractionResult(provider="openai", terms=_parse_terms_payload(payload), raw=raw)


def _extract_terms_lightcone(
    report_text: str,
    *,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
) -> TermExtractionResult:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install dependencies first: python -m pip install -r requirements.txt") from exc

    resolved_key = load_secret_from_env_or_shell_rc("TZAFON_API_KEY", api_key)
    if not resolved_key:
        raise RuntimeError("Set TZAFON_API_KEY or paste a Lightcone/Tzafon API key.")

    client = OpenAI(api_key=resolved_key, base_url=base_url or os.getenv("TZAFON_BASE_URL", DEFAULT_TZAFON_BASE_URL))
    completion = client.chat.completions.create(
        model=model or os.getenv("TZAFON_MODEL", DEFAULT_TZAFON_MODEL),
        temperature=0,
        messages=[
            {"role": "system", "content": _term_extraction_instructions() + "\nReturn only a JSON object."},
            {"role": "user", "content": report_text},
        ],
    )
    raw = _dump_response(completion)
    content = completion.choices[0].message.content or "{}"
    payload = _parse_json_text(content)
    return TermExtractionResult(provider="lightcone", terms=_parse_terms_payload(payload), raw=raw)


def _term_extraction_instructions() -> str:
    return (
        "Extract terms from a medical imaging report that a vision model should try to localize on images. "
        "Return anatomical structures, lesions, devices, named regions, measurements tied to visible regions, "
        "and directional/positional findings. Keep terms short but preserve laterality and modifiers. "
        "Do not diagnose, recommend treatment, or add terms not grounded in the report. "
        "If a report has repeated mentions, return one canonical term with useful aliases."
    )


def _terms_schema() -> dict[str, Any]:
    term_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "query": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "context": {"type": "string"},
            "source_sentence": {"type": "string"},
        },
        "required": ["name", "query", "aliases", "context", "source_sentence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "terms": {"type": "array", "items": term_schema},
            "notes": {"type": "string"},
        },
        "required": ["terms", "notes"],
        "additionalProperties": False,
    }


def _parse_terms_payload(payload: Any, *, limit: int = DEFAULT_TERM_LIMIT) -> list[ReportTerm]:
    if isinstance(payload, list):
        raw_terms = payload
    elif isinstance(payload, dict):
        raw_terms = payload.get("terms", [])
    else:
        raw_terms = []

    terms: list[ReportTerm] = []
    seen: set[str] = set()
    for raw_term in raw_terms:
        term = _coerce_report_term(raw_term)
        if term is None:
            continue
        key = _normalize_term_key(term.name)
        if not key or key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _coerce_report_term(raw_term: Any) -> ReportTerm | None:
    if isinstance(raw_term, str):
        name = raw_term.strip()
        return ReportTerm(name=name, query=name) if name else None

    if not isinstance(raw_term, dict):
        return None

    name = str(raw_term.get("name") or raw_term.get("term") or raw_term.get("label") or "").strip()
    if not name:
        return None

    aliases_raw = raw_term.get("aliases", [])
    aliases = [str(alias).strip() for alias in aliases_raw if str(alias).strip()] if isinstance(aliases_raw, list) else []
    query = str(raw_term.get("query") or name).strip()
    return ReportTerm(
        name=name,
        query=query or name,
        aliases=aliases,
        context=str(raw_term.get("context") or "").strip(),
        source_sentence=str(raw_term.get("source_sentence") or raw_term.get("source") or "").strip(),
    )


def _normalize_term_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _parse_json_text(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)

    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        if _field(item, "type") != "message":
            continue
        for block in _field(item, "content", []) or []:
            text = _field(block, "text")
            if text:
                parts.append(str(text))
    return "\n".join(parts)


def _load_shell_rc_value(name: str) -> str | None:
    for path in _shell_rc_paths():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        value = _extract_exported_env_var(text, name)
        if value:
            return value
    return None


def _shell_rc_paths() -> list[Path]:
    home = Path.home()
    return [
        home / ".bashrc",
        home / ".bash_profile",
        home / ".profile",
        home / ".zshrc",
    ]


def _extract_exported_env_var(text: str, name: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if not stripped.startswith(name + "="):
            continue
        value = stripped.split("=", 1)[1].strip()
        if not value:
            return None
        try:
            parsed = shlex.split(value, comments=False, posix=True)
        except ValueError:
            parsed = []
        return parsed[0] if parsed else value.strip("'\"")
    return None


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _dump_response(response: Any) -> str:
    for method_name in ("model_dump_json", "to_json"):
        method = getattr(response, method_name, None)
        if callable(method):
            try:
                return method(indent=2)
            except TypeError:
                try:
                    return method()
                except TypeError:
                    pass
    return repr(response)
