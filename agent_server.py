import os
import uuid
import logging
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("a2a-travel-agent")

app = FastAPI(title="Gemini Travel Risk Agent (A2A)")

# 環境変数からAPIキーを取得
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# 現在有効なGeminiモデル候補（上から順に試行）
MODEL_CANDIDATES = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]

SYSTEM_INSTRUCTION = """
あなたは企業の海外出張リスク分析の専門アドバイザーです。
ユーザーから渡航先・期間・目的などの情報が入力されたら、以下の4項目を簡潔かつ実務的に分析して回答してください。
1. 治安・安全リスク（渡航先の現在の一般的な注意点）
2. 健康・医療・感染症対策（必要な予防接種や衛生面の注意）
3. 入国・ビザ・現地法規の注意点
4. 出張者への推奨アクション（事前準備リスト）
"""


def call_gemini(prompt: str) -> str:
    if not client:
        raise RuntimeError("GEMINI_API_KEY が設定されていません。")

    last_error = None
    for model_name in MODEL_CANDIDATES:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.3,
                ),
            )
            if response.text:
                logger.info(f"Success with model: {model_name}")
                return response.text
        except Exception as e:
            logger.warning(f"Model {model_name} failed: {e}")
            last_error = e
            continue

    raise RuntimeError(f"すべての候補モデルで呼び出しに失敗しました: {last_error}")


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
    return {"status": "ok", "api_key_configured": bool(GEMINI_API_KEY)}


# A2A公式のAgent Cardエンドポイント（新旧両方のパスに対応）
@app.get("/.well-known/agent-card.json")
@app.get("/.well-known/agent.json")
async def get_agent_card(request: Request):
    # リバースプロキシ（Render等）配下でも正しいhttps URLを生成
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    base_url = f"{forwarded_proto}://{host}"
    return JSONResponse(content=build_agent_card(base_url))


def extract_user_text(params: Dict[str, Any]) -> str:
    """A2Aの message/send または tasks/send パラメータからテキストを抽出"""
    message = params.get("message", {})
    parts = message.get("parts", [])
    texts = []
    for part in parts:
        if isinstance(part, dict):
            if "text" in part:
                texts.append(part["text"])
            elif part.get("kind") == "text" and "text" in part:
                texts.append(part["text"])
    if texts:
        return "\n".join(texts)
    # フォールバック（直接テキストが渡された場合）
    return str(params.get("input", "海外出張のリスクを教えてください。"))


# A2A JSON-RPC 2.0 メインエンドポイント
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

    if method in ("message/send", "tasks/send"):
        user_text = extract_user_text(params)
        try:
            reply_text = call_gemini(user_text)
        except Exception as e:
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

        # A2A標準のTask完了レスポンス形式
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
