#!/usr/bin/env python3
"""Book-title based adaptive reading-model selector.

This module is intentionally dependency-light. It uses only stdlib + PyYAML.
Search and DeepSeek calls are optional; when they are not configured or fail,
it falls back to deterministic heuristics so the batch workflow can continue.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("请先安装 PyYAML: pip install pyyaml") from exc

BASE_DIR = Path(__file__).parent.resolve()
MODEL_SELECTION_FILE = BASE_DIR / "prompts" / "model_selection.yaml"
CACHE_DIR = BASE_DIR / ".cache" / "book_model_selection"


def load_selection_config(path: Path = MODEL_SELECTION_FILE) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"未找到模型选择配置: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "models" not in data:
        raise ValueError("model_selection.yaml 缺少 models 配置")
    return data


def _cache_path(book_title: str, suffix: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = hashlib.md5(book_title.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{slug}_{suffix}.json"


def _json_request(url: str, *, method: str = "GET", headers: Dict[str, str] | None = None,
                  payload: Dict[str, Any] | None = None, timeout: int = 20) -> Dict[str, Any]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user configured endpoint
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def search_book_metadata(book_title: str, *, enabled: bool = True, force: bool = False) -> Dict[str, Any]:
    """Search public web metadata for a book title.

    Provider priority:
    1. BOOK_SEARCH_PROVIDER=serper + SERPER_API_KEY
    2. BOOK_SEARCH_PROVIDER=tavily + TAVILY_API_KEY
    3. BOOK_SEARCH_PROVIDER=brave + BRAVE_SEARCH_API_KEY
    4. BOOK_SEARCH_PROVIDER=ddg or auto fallback: DuckDuckGo HTML lite
    5. none/failure: return empty results
    """
    cache = _cache_path(book_title, "search")
    if cache.exists() and not force:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not enabled:
        result = {"query": book_title, "provider": "disabled", "results": [], "error": "search disabled"}
        cache.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    provider = os.getenv("BOOK_SEARCH_PROVIDER", "auto").lower().strip()
    query = f"{book_title} 书籍 作者 内容 简介 主题 类型"
    result: Dict[str, Any] = {"query": query, "provider": provider, "results": []}

    try:
        if provider in {"auto", "serper"} and os.getenv("SERPER_API_KEY"):
            data = _json_request(
                "https://google.serper.dev/search",
                method="POST",
                headers={"X-API-KEY": os.environ["SERPER_API_KEY"], "Content-Type": "application/json"},
                payload={"q": query, "num": 6, "hl": "zh-cn"},
            )
            items = data.get("organic", [])[:6]
            result = {"query": query, "provider": "serper", "results": [
                {"title": i.get("title", ""), "snippet": i.get("snippet", ""), "url": i.get("link", "")} for i in items
            ]}
        elif provider in {"auto", "tavily"} and os.getenv("TAVILY_API_KEY"):
            data = _json_request(
                "https://api.tavily.com/search",
                method="POST",
                headers={"Content-Type": "application/json"},
                payload={"api_key": os.environ["TAVILY_API_KEY"], "query": query, "max_results": 6, "search_depth": "basic"},
            )
            items = data.get("results", [])[:6]
            result = {"query": query, "provider": "tavily", "results": [
                {"title": i.get("title", ""), "snippet": i.get("content", ""), "url": i.get("url", "")} for i in items
            ]}
        elif provider in {"auto", "brave"} and os.getenv("BRAVE_SEARCH_API_KEY"):
            url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": 6})
            data = _json_request(url, headers={"X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"]})
            items = data.get("web", {}).get("results", [])[:6]
            result = {"query": query, "provider": "brave", "results": [
                {"title": i.get("title", ""), "snippet": i.get("description", ""), "url": i.get("url", "")} for i in items
            ]}
        elif provider in {"auto", "ddg"}:
            # Best-effort no-key fallback. If blocked, selector still falls back to heuristics.
            url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                text = resp.read().decode("utf-8", errors="replace")
            titles = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', text, flags=re.S)[:6]
            snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>|<div[^>]+class="result__snippet"[^>]*>(.*?)</div>', text, flags=re.S)[:6]
            urls = re.findall(r'<a[^>]+class="result__a"[^>]+href="(.*?)"', text, flags=re.S)[:6]
            cleaned = []
            for idx, title in enumerate(titles):
                snip = ""
                if idx < len(snippets):
                    pair = snippets[idx]
                    snip = pair[0] or pair[1] if isinstance(pair, tuple) else str(pair)
                cleaned.append({
                    "title": html.unescape(re.sub(r"<.*?>", "", title)).strip(),
                    "snippet": html.unescape(re.sub(r"<.*?>", "", snip)).strip(),
                    "url": html.unescape(urls[idx]).strip() if idx < len(urls) else "",
                })
            result = {"query": query, "provider": "ddg", "results": cleaned}
        else:
            result = {"query": query, "provider": provider, "results": [], "error": "no provider configured"}
    except Exception as exc:  # keep workflow alive
        result = {"query": query, "provider": provider, "results": [], "error": f"{type(exc).__name__}: {exc}"}

    cache.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _extract_json_object(text: str) -> Dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _deepseek_chat(messages: List[Dict[str, str]], *, temperature: float = 0.1, timeout: int = 45) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY/LLM_API_KEY 未配置")

    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", os.getenv("LLM_MODEL", "deepseek-chat"))
    url = f"{base_url}/chat/completions"
    data = _json_request(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        payload={"model": model, "messages": messages, "temperature": temperature, "response_format": {"type": "json_object"}},
        timeout=timeout,
    )
    return data["choices"][0]["message"]["content"]


def _model_descriptions(config: Dict[str, Any]) -> str:
    lines = []
    for key, meta in config["models"].items():
        lines.append(f"- {key}: {meta.get('label')}。适合：{meta.get('best_for')}")
    return "\n".join(lines)


def llm_select_models(book_title: str, search_data: Dict[str, Any], config: Dict[str, Any],
                      *, min_models: int, max_models: int) -> Dict[str, Any]:
    model_keys = list(config["models"].keys())
    search_summary = "\n".join(
        f"{idx+1}. {r.get('title','')} — {r.get('snippet','')}"
        for idx, r in enumerate(search_data.get("results", [])[:6])
    ) or "无可用搜索结果。"
    system = "你是一个书籍分析工作流调度器，任务是根据书名和检索资料选择最合适的阅读分析模型。必须输出 JSON。"
    user = f"""
书名：{book_title}

检索资料：
{search_summary}

可选模型：
{_model_descriptions(config)}

选择规则：
1. 不要一次性选择所有模型。
2. 默认选择 {min_models}-{max_models} 个模型。
3. 通常必须包含 structure_quick，作为第一轮全书知识地图。
4. 如果是社会、经济、制度、商业、历史、组织问题，优先 causal；涉及权力、利益、制度冲突时加 power。
5. 如果是概念密集、理论性强，优先 concept。
6. 如果作者提出强主张或争议观点，加入 axiom 或 blindspot。
7. 如果是反常识、科学证据、认知刷新类，加入 bayes。
8. 如果是实践方法、管理、学习、个人成长，加入 practice。
9. 如果是创新、技术、战略、从零重构类，加入 first_principles。
10. 如果是哲学、价值冲突、思想史、政治文化，加入 dialectic。
11. structure 和 structure_quick 功能相近，默认不要同时选择。

请输出严格 JSON，格式：
{{
  "book_profile": {{
    "book_type": "...",
    "domains": ["..."],
    "likely_genre": "...",
    "analysis_needs": ["..."]
  }},
  "selected_models": ["structure_quick", "..."],
  "rationale": "选择理由，说明为什么这些模型比其他模型更合适",
  "rejected_models": {{"model_key": "不选原因"}}
}}

只能从这些模型 key 中选择：{', '.join(model_keys)}
""".strip()
    content = _deepseek_chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
    data = _extract_json_object(content)
    if not data:
        raise RuntimeError("DeepSeek 没有返回合法 JSON")
    data["source"] = "deepseek"
    return data


def heuristic_select_models(book_title: str, search_data: Dict[str, Any], config: Dict[str, Any],
                            *, min_models: int, max_models: int) -> Dict[str, Any]:
    text = book_title + " " + " ".join(
        (r.get("title", "") + " " + r.get("snippet", "")) for r in search_data.get("results", [])[:6]
    )
    selected: List[str] = ["structure_quick"]
    needs: List[str] = ["先建立全书知识地图"]

    # Small local title hints for commonly analyzed Chinese books.
    # This helps when --no-search --no-llm is used.
    for hint, hinted_models in (config.get("title_hints") or {}).items():
        if hint and hint in book_title:
            selected = []
            for key in hinted_models:
                if key in config["models"] and key not in selected:
                    selected.append(key)
            needs.append(f"命中书名规则：{hint}")
            break

    def add(key: str, reason: str) -> None:
        if key not in selected:
            selected.append(key)
            needs.append(reason)

    rules: List[Tuple[str, str, List[str]]] = [
        (r"经济|制度|社会|国家|城市|贫困|增长|金融|市场|历史|人口|治理|政策|教育|医疗|组织|公司|商业|平台|管理", "causal", ["分析社会/商业/制度现象背后的因果机制"]),
        (r"权力|政治|阶层|阶级|利益|博弈|资本|政府|制度|治理|不平等|平台|垄断|劳动|组织", "power", ["识别行动者、利益格局和权力结构"]),
        (r"哲学|思想|主义|伦理|文化|现代性|自由|正义|民主|文明|价值|意义", "dialectic", ["分析正反张力和价值冲突"]),
        (r"概念|理论|心理|认知|传播|语言|叙事|框架|模型|原则|系统", "concept", ["拆解关键概念和概念边界"]),
        (r"方法|实践|习惯|效率|学习|写作|成长|沟通|决策|领导|管理|训练|如何", "practice", ["转化为行动清单和实践计划"]),
        (r"科学|证据|实验|概率|统计|反常识|认知|大脑|心理|事实|研究", "bayes", ["评估证据强度和信念更新幅度"]),
        (r"创新|技术|战略|创业|未来|产品|设计|工程|从零|第一性|马斯克", "first_principles", ["从底层事实重建解释或方案"]),
        (r"批判|争议|真相|谎言|迷思|误区|陷阱|反思", "blindspot", ["寻找作者观点盲区、反例和误读风险"]),
        (r"论证|证明|逻辑|学术|理论|范式|研究", "axiom", ["审查作者推理链条和核心前提"]),
    ]
    for pattern, key, reasons in rules:
        if re.search(pattern, text, flags=re.I):
            add(key, reasons[0])

    if len(selected) < min_models:
        for key in config.get("model_policy", {}).get("fallback_models", ["structure_quick", "concept", "blindspot"]):
            add(key, "通用兜底阅读视角")
            if len(selected) >= min_models:
                break

    # If too many, keep strongest practical mix. Preserve order.
    if len(selected) > max_models:
        selected = selected[:max_models]

    return {
        "source": "heuristic",
        "book_profile": {
            "book_type": "根据书名和检索片段启发式判断",
            "domains": [],
            "likely_genre": "unknown",
            "analysis_needs": needs,
        },
        "selected_models": selected,
        "rationale": "未使用 DeepSeek 或 DeepSeek 调用失败，已根据书名关键词和搜索片段进行启发式选择：" + "；".join(needs),
        "rejected_models": {},
    }


def normalize_selection(selection: Dict[str, Any], config: Dict[str, Any], *, min_models: int, max_models: int) -> Dict[str, Any]:
    valid = set(config["models"].keys())
    selected = [m for m in selection.get("selected_models", []) if m in valid]
    policy = config.get("model_policy", {})

    for m in policy.get("always_include", []):
        if m in valid and m not in selected:
            selected.insert(0, m)

    # Avoid structure + structure_quick duplication unless explicitly produced and max allows; prefer structure_quick.
    if "structure" in selected and "structure_quick" in selected:
        selected = [m for m in selected if m != "structure"]

    for m in policy.get("fallback_models", []):
        if len(selected) >= min_models:
            break
        if m in valid and m not in selected:
            selected.append(m)

    selected = selected[:max_models]
    selection["selected_models"] = selected
    if not selection.get("rationale"):
        selection["rationale"] = "根据默认策略选择模型。"
    return selection


def select_models_for_book(book_title: str, *, search_enabled: bool = True, force_search: bool = False,
                           min_models: int | None = None, max_models: int | None = None,
                           use_llm: bool = True) -> Dict[str, Any]:
    config = load_selection_config()
    policy = config.get("model_policy", {})
    min_models = min_models or int(policy.get("default_min_models", 3))
    max_models = max_models or int(policy.get("default_max_models", 4))

    search_data = search_book_metadata(book_title, enabled=search_enabled, force=force_search)
    cache = _cache_path(book_title, "selection")
    if cache.exists() and not force_search:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            return normalize_selection(cached, config, min_models=min_models, max_models=max_models)
        except Exception:
            pass

    selection: Dict[str, Any]
    if use_llm:
        try:
            selection = llm_select_models(book_title, search_data, config, min_models=min_models, max_models=max_models)
        except Exception as exc:
            selection = heuristic_select_models(book_title, search_data, config, min_models=min_models, max_models=max_models)
            selection["llm_error"] = f"{type(exc).__name__}: {exc}"
    else:
        selection = heuristic_select_models(book_title, search_data, config, min_models=min_models, max_models=max_models)

    selection = normalize_selection(selection, config, min_models=min_models, max_models=max_models)
    selection["book_title"] = book_title
    selection["search"] = search_data
    selection["selected_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    cache.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
    return selection


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="按书名推荐阅读分析模型")
    parser.add_argument("title", help="书名")
    parser.add_argument("--no-search", action="store_true", help="不做网络检索，只按书名启发式/LLM 判断")
    parser.add_argument("--no-llm", action="store_true", help="不调用 DeepSeek，只用启发式规则")
    parser.add_argument("--force", action="store_true", help="忽略缓存重新检索和选择")
    parser.add_argument("--min-models", type=int, default=None)
    parser.add_argument("--max-models", type=int, default=None)
    args = parser.parse_args()
    print(json.dumps(select_models_for_book(
        args.title,
        search_enabled=not args.no_search,
        force_search=args.force,
        min_models=args.min_models,
        max_models=args.max_models,
        use_llm=not args.no_llm,
    ), ensure_ascii=False, indent=2))
