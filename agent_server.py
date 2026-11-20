import asyncio
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("a2a-travel-agent")

app = FastAPI(title="Gemini Travel Risk Agent (A2A)")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# 環境変数 GEMINI_MODEL があれば最優先、なければ gemini-2.0-flash を使用
PRIMARY_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"

# モデル選定方針:
#   Search Grounding が使えるのは Gemini 2 / 2.5 系のみ（Gemini 3 以降は RPD=0/0 で利用不可）
#   gemini-2.0-flash:      search=True 対応、RPD=1,500 ← 本命（軽量・高速）
#   gemini-2.5-flash-lite: search=True 対応、RPD=1,500 ← 次点（404の場合はスキップ）
#   gemini-3.1-flash-lite: search=False のみ有効、RPD=500 ← Grounding なしフォールバック
#   gemma-4-26b-a4b-it:    search=False、RPD=14.4K ← 最終保険
PREFERRED_MODELS = [
    PRIMARY_MODEL,           # gemini-2.0-flash (search=True 有効)
    "gemini-2.5-flash-lite", # search=True 有効（404なら自動スキップ）
    "gemini-3.1-flash-lite", # search=False のみ（Grounding なし）
    "gemma-4-26b-a4b-it",    # 最終保険（search=False）
]

# 環境変数 SYSTEM_INSTRUCTION が未設定のときに使われるシンプルなデフォルト指示
DEFAULT_SYSTEM_INSTRUCTION = """
あなたは企業の海外出張リスク分析の専門アドバイザーです。
ユーザーから渡航先・期間・目的などの情報が入力されたら、以下の4項目を簡潔かつ実務的に分析して回答してください。
1. 治安・安全リスク（渡航先の現在の一般的な注意点）
2. 健康・医療・感染症対策（必要な予防接種や衛生面の注意）
3. 入国・ビザ・現地法規・ビジネス文化の注意点
4. 出張者への推奨アクション（事前準備リスト）
"""

# Render の Environment Variables に SYSTEM_INSTRUCTION を設定すればそちらが優先される
SYSTEM_INSTRUCTION = os.environ.get("SYSTEM_INSTRUCTION", "").strip() or DEFAULT_SYSTEM_INSTRUCTION


def search_travel_context(destination: str) -> Optional[str]:
    """Tavily で渡航先の最新情報を検索して返す。APIキー未設定または失敗時は None を返す。"""
    if not TAVILY_API_KEY:
        logger.info("TAVILY_API_KEY not set, skipping web search")
        return None
    try:
        from tavily import TavilyClient
        tavily = TavilyClient(api_key=TAVILY_API_KEY)
        query = f"{destination} 渡航 治安 最新情報 2025"
        logger.info(f"Tavily search: {query}")
        result = tavily.search(query=query, max_results=3, search_depth="basic")
        snippets = []
        for item in result.get("results", []):
            title = item.get("title", "")
            content = item.get("content", "")
            url = item.get("url", "")
            if content:
                snippets.append(f"【{title}】{content[:300]} (出典: {url})")
        if snippets:
            context = "\n\n".join(snippets)
            logger.info(f"Tavily search returned {len(snippets)} results")
            return context
    except Exception as e:
        logger.warning(f"Tavily search failed: {e}")
    return None


def build_prompt_with_context(user_text: str) -> str:
    """ユーザー入力から渡航先を推定し、Tavily で最新情報を取得してプロンプトに付加する。"""
    context = search_travel_context(user_text)
    if context:
        return (
            f"{user_text}\n\n"
            f"---\n"
            f"【参考：Web検索による最新情報】\n{context}\n"
            f"---\n"
            f"上記の最新情報も踏まえて回答してください。"
        )
    return user_text


def get_model_candidates() -> List[str]:
    candidates: List[str] = []
    for m in PREFERRED_MODELS:
        cleaned = (m or "").strip()
        if not cleaned:
            continue
        short = cleaned.replace("models/", "")
        if short not in candidates:
            candidates.append(short)
    return candidates


def _extract_response_text(response: Any) -> str:
    direct_text = getattr(response, "text", None)
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()

    parts_text: List[str] = []
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            if getattr(part, "thought", False):
                continue
            p_text = getattr(part, "text", None)
            if isinstance(p_text, str) and p_text.strip():
                parts_text.append(p_text.strip())

    return "\n".join(parts_text).strip()


def _invoke_model(model_name: str, prompt: str, use_search: bool) -> str:
    logger.info(f"Calling Gemini model: {model_name} (search={use_search})")
    tools = [types.Tool(google_search=types.GoogleSearch())] if use_search else None
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.3,
        tools=tools,
    )
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=config,
    )
    text = _extract_response_text(response)
    if not text:
        finish_reasons = [
            getattr(c, "finish_reason", None) for c in (getattr(response, "candidates", None) or [])
        ]
        raise RuntimeError(f"empty response (finish_reasons={finish_reasons})")
    logger.info(f"Success with model: {model_name} (search={use_search})")
    return text


def call_gemini_sync(prompt: str) -> str:
    import time

    if not client:
        raise RuntimeError("GEMINI_API_KEY が設定されていません。")

    errors: List[str] = []
    for model_name in get_model_candidates():
        supports_search = model_name.startswith("gemini-")
        attempt_modes = [True, False] if supports_search else [False]

        for use_search in attempt_modes:
            try:
                return _invoke_model(model_name, prompt, use_search=use_search)
            except Exception as e:
                err_str = str(e)
                logger.warning(f"Model {model_name} (search={use_search}) failed: {err_str}")
                errors.append(f"{model_name}[search={use_search}]: {err_str}")

                # クォータ上限（429）の場合、search=True で失敗した場合は search=False も試す
                # search=False でも 429 なら次のモデルへスキップ
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    if use_search:
                        continue  # search=False で再試行
                    else:
                        break     # search=False でも 429 → 次のモデルへ

                # 一時的な高負荷（503）の場合は3秒待ってから同一構成で1回だけリトライ
                if "503" in err_str or "UNAVAILABLE" in err_str:
                    logger.info(f"Model {model_name} returned 503, retrying once after 3s...")
                    time.sleep(3)
                    try:
                        return _invoke_model(model_name, prompt, use_search=use_search)
                    except Exception as e2:
                        err_str2 = str(e2)
                        logger.warning(f"Model {model_name} (search={use_search}) retry also failed: {err_str2}")
                        errors[-1] += f" [retry: {err_str2}]"
                    break

                continue

    raise RuntimeError("すべての候補モデルで呼び出しに失敗しました: " + " | ".join(errors))


async def call_gemini(prompt: str) -> str:
    return await asyncio.to_thread(call_gemini_sync, prompt)


def build_agent_card(base_url: str) -> Dict[str, Any]:
    return {
        "name": "Overseas Travel Risk Analyst (Gemini)",
        "description": "海外出張先の治安・医療・渡航リスクを分析し、事前対策を提示する専門エージェントです。",
        "url": base_url,
        "version": "1.0.0",
        "protocolVersion": "0.2.5",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "travel_risk_assessment",
                "name": "海外出張リスク分析",
                "description": "渡航先や日程に基づき、治安・健康・入国手続きのリスクと推奨アクションを分析します。",
                "tags": ["travel", "risk", "safety", "compliance"],
                "examples": [
                    "来月、インドのベンガルールに5日間出張します。リスクと準備事項を教えてください。"
                ],
            }
        ],
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "api_key_configured": bool(GEMINI_API_KEY),
        "tavily_configured": bool(TAVILY_API_KEY),
        "custom_system_instruction": bool(os.environ.get("SYSTEM_INSTRUCTION", "").strip()),
        "primary_model": PRIMARY_MODEL,
    }


@app.get("/models")
async def models_endpoint():
    if not client:
        return {"available_models": []}
    names: List[str] = []
    try:
        for m in client.models.list():
            name = getattr(m, "name", "")
            if name:
                names.append(name)
    except Exception as e:
        logger.warning(f"Failed to list models: {e}")
    return {"available_models": names}


@app.get("/.well-known/agent-card.json")
@app.get("/.well-known/agent.json")
async def get_agent_card(request: Request):
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    base_url = f"{forwarded_proto}://{host}"
    return JSONResponse(content=build_agent_card(base_url))


def extract_user_text(params: Dict[str, Any]) -> str:
    message = params.get("message", {})
    parts = message.get("parts", [])
    texts: List[str] = []
    for part in parts:
        if isinstance(part, dict):
            if "text" in part and part["text"] is not None:
                texts.append(str(part["text"]))
            elif part.get("kind") == "text" and "text" in part and part["text"] is not None:
                texts.append(str(part["text"]))
    if texts:
        return "\n".join(texts)
    return str(params.get("input", "海外出張のリスクを教えてください。"))


@app.post("/")
async def a2a_rpc_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
        )

    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})
    logger.info(f"Received A2A RPC method={method} id={rpc_id}")

    if method in ("message/send", "tasks/send"):
        user_text = extract_user_text(params)
        prompt = await asyncio.to_thread(build_prompt_with_context, user_text)
        try:
            reply_text = await call_gemini(prompt)
        except Exception as e:
            logger.error(f"A2A call_gemini failed: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32000, "message": str(e)},
                },
            )

        task_id = params.get("id") or str(uuid.uuid4())
        context_id = params.get("contextId") or str(uuid.uuid4())
        msg_id = str(uuid.uuid4())

        result = {
            "id": task_id,
            "contextId": context_id,
            "kind": "task",
            "status": {
                "state": "completed",
                "message": {
                    "role": "agent",
                    "parts": [{"kind": "text", "text": reply_text}],
                    "messageId": msg_id,
                },
            },
            "artifacts": [
                {
                    "artifactId": str(uuid.uuid4()),
                    "name": "Travel Risk Report",
                    "parts": [{"kind": "text", "text": reply_text}],
                }
            ],
        }
        return JSONResponse(content={"jsonrpc": "2.0", "id": rpc_id, "result": result})

    return JSONResponse(
        status_code=400,
        content={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        },
    )
