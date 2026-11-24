import asyncio
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("a2a-travel-agent")

app = FastAPI(title="Gemini Travel Risk Agent (A2A)")

# 静的ファイル配信（カレントディレクトリまたは /app 以下の static ディレクトリ）
_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
logger.info(f"Static dir: {_static_dir}, exists: {os.path.isdir(_static_dir)}")
if os.path.isdir(_static_dir):
    app.mount("/ui", StaticFiles(directory=_static_dir, html=True), name="static")
    logger.info("Static files mounted at /ui")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# 環境変数 GEMINI_MODEL があれば最優先、なければ gemini-3.1-flash-lite を使用
PRIMARY_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip() or "gemini-3.1-flash-lite"

# モデル選定方針:
#   Tavily で検索結果をプロンプトに付加する構成のため Google Search Grounding は不要。
#   gemini-2.0-flash / gemini-2.5-flash-lite は廃止済み（404）のため除去。
#   gemini-3.1-flash-lite: search=False、RPD=500 ← プライマリ
#   gemma-4-26b-a4b-it:    search=False、RPD=14.4K ← 最終保険
PREFERRED_MODELS = [
    PRIMARY_MODEL,        # gemini-3.1-flash-lite（プライマリ）
    "gemma-4-26b-a4b-it", # 最終保険（search=False）
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


def extract_travel_info(user_text: str) -> Dict[str, Any]:
    """Gemini を使ってユーザー入力から渡航先と渡航月リスト（年付き）を抽出する。
    返り値例: {"destination": "バンコク", "periods": [{"year": 2026, "month": 11}, {"year": 2026, "month": 12}]}
    失敗時は {"destination": user_text[:30], "periods": []} を返す。
    """
    import datetime
    fallback = {"destination": user_text[:30].strip(), "periods": []}
    if not client:
        return fallback
    try:
        import json
        now = datetime.datetime.now()
        config = types.GenerateContentConfig(temperature=0.0)
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=(
                f"今日は{now.year}年{now.month}月{now.day}日です。\n"
                "次の文章から渡航先と渡航期間に含まれる年月をすべて抽出してください。\n"
                "必ず以下のJSON形式のみを出力してください。それ以外は一切出力しないでください。\n"
                '{"destination": "都市名または国名", "periods": [{"year": 年, "month": 月}, ...]}\n'
                "periodsは渡航開始月から終了月まで順番にすべて列挙してください。年をまたぐ場合も正確に。\n"
                "渡航先が不明な場合はdestinationを空文字にしてください。\n\n"
                f"文章：{user_text}"
            ),
            config=config,
        )
        raw = _extract_response_text(response).strip()
        raw = raw.strip("` \n").removeprefix("json").strip()
        data = json.loads(raw)
        destination = str(data.get("destination", "")).strip()
        periods = [
            {"year": int(p["year"]), "month": int(p["month"])}
            for p in data.get("periods", [])
            if 1 <= int(p.get("month", 0)) <= 12
        ]
        if destination:
            logger.info(f"Extracted travel info: destination={destination!r}, periods={periods}")
            return {"destination": destination, "periods": periods}
    except Exception as e:
        logger.warning(f"Travel info extraction failed: {e}")
    return fallback


# 外務省オープンデータ：都市名→国コードのマッピング（主要渡航先）
# https://www.ezairyu.mofa.go.jp/opendata/country/{CODE}.xml
_CITY_TO_MOFA_CODE: dict = {
    # 東南アジア
    "バンコク": "0066", "タイ": "0066",
    "シンガポール": "0065",
    "クアラルンプール": "0060", "マレーシア": "0060",
    "ジャカルタ": "0062", "バリ": "0062", "インドネシア": "0062",
    "マニラ": "0063", "フィリピン": "0063",
    "ホーチミン": "0084", "ハノイ": "0084", "ベトナム": "0084",
    "ヤンゴン": "0095", "ミャンマー": "0095",
    "プノンペン": "0855", "カンボジア": "0855",
    # 東アジア
    "上海": "0086", "北京": "0086", "中国": "0086",
    "ソウル": "0082", "韓国": "0082",
    "台北": "0886", "台湾": "0886",
    "香港": "00852",
    # 南アジア
    "ムンバイ": "0091", "デリー": "0091", "インド": "0091",
    # 中東
    "ドバイ": "00971", "アブダビ": "00971", "UAE": "00971",
    "リヤド": "00966", "サウジアラビア": "00966",
    "イスラエル": "00972",
    "イラク": "00964",
    "イラン": "0098",
    # 欧州
    "パリ": "0033", "フランス": "0033",
    "ロンドン": "0044", "イギリス": "0044",
    "ベルリン": "0049", "フランクフルト": "0049", "ドイツ": "0049",
    "ローマ": "0039", "イタリア": "0039",
    "マドリード": "0034", "スペイン": "0034",
    "モスクワ": "007", "サンクトペテルブルク": "007", "ロシア": "007",
    "イスタンブール": "0090", "トルコ": "0090",
    "ウクライナ": "00380",
    # 北米
    "ニューヨーク": "001", "ロサンゼルス": "001", "シカゴ": "001", "アメリカ": "001",
    "トロント": "001CA", "カナダ": "001CA",
    # 大洋州
    "シドニー": "0061", "メルボルン": "0061", "オーストラリア": "0061",
    # アフリカ
    "ナイロビ": "00254", "ケニア": "00254",
    "ヨハネスブルグ": "0027", "南アフリカ": "0027",
}

# 外務省オープンデータの国コード（4桁）→国名（キャッシュ用）
_mofa_code_cache: dict = {}


def _get_mofa_country_code(destination: str) -> Optional[str]:
    """渡航先名から外務省オープンデータの国コードを返す。見つからなければ None。"""
    # 完全一致優先
    if destination in _CITY_TO_MOFA_CODE:
        return _CITY_TO_MOFA_CODE[destination]
    # 部分一致
    for key, code in _CITY_TO_MOFA_CODE.items():
        if key in destination or destination in key:
            return code
    return None


def get_mofa_safety_level(destination: str) -> str:
    """外務省オープンデータから渡航先の危険度レベルを取得して返す。
    Tavily・Gemini 不要。外務省公式 XML を直接取得（5分毎更新）。
    返り値例: "一部地域レベル3（渡航中止勧告）/ 一部地域レベル2（不要不急の渡航自粛）"
    取得失敗時は空文字を返す。
    """
    import urllib.request
    import xml.etree.ElementTree as ET

    code = _get_mofa_country_code(destination)
    if not code:
        logger.warning(f"MOFA: no country code for {destination!r}")
        return ""

    url = f"https://www.ezairyu.mofa.go.jp/opendata/country/{code}.xml"
    logger.info(f"MOFA opendata fetch: {url}")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            content = resp.read().decode("utf-8")
        root = ET.fromstring(content)

        # riskLevel1〜4 フラグを読む（1=発令中）
        LEVEL_LABELS = {
            "4": "レベル4（退避勧告）",
            "3": "レベル3（渡航中止勧告）",
            "2": "レベル2（不要不急の渡航自粛）",
            "1": "レベル1（十分注意）",
        }
        active = []
        for lv in ["4", "3", "2", "1"]:
            elem = root.find(f"riskLevel{lv}")
            if elem is not None and elem.text == "1":
                active.append(LEVEL_LABELS[lv])

        if not active:
            # 危険情報なし＝全域レベル1未満（安全）
            result = "危険情報の発令なし（概ね安全）"
        else:
            result = " / ".join(active)

        # riskTitle も付加（例：「タイの危険情報【一部地域の危険レベル引下げ】」）
        title_elem = root.find("riskTitle")
        if title_elem is not None and title_elem.text:
            result = f"{result}（{title_elem.text}）"

        logger.info(f"MOFA level for {destination!r} [{code}]: {result}")
        return result
    except Exception as e:
        logger.warning(f"MOFA opendata fetch failed for {destination!r}: {e}", exc_info=True)
        return ""


def search_travel_context(user_text: str) -> Optional[str]:
    """渡航先・渡航月を抽出し、Tavily で最新情報を検索して返す。APIキー未設定または失敗時は None を返す。"""
    if not TAVILY_API_KEY:
        logger.info("TAVILY_API_KEY not set, skipping web search")
        return None
    import datetime
    info = extract_travel_info(user_text)
    destination = info["destination"]
    periods = info["periods"]
    now = datetime.datetime.now()
    # 渡航期間の年月文字列を生成（年をまたぐ場合も正確に）
    if periods:
        period_str = "・".join(f"{p['year']}年{p['month']}月" for p in periods)
    else:
        period_str = f"{now.year}年{now.month}月"
    queries = [
        f"{destination} 治安 安全情報 最新",
        f"{destination} {period_str} 気候 季節",
        f"{destination} {period_str} イベント 祝日",
    ]
    # 出発が今月または翌月以内なら天気予報クエリを追加
    if periods:
        first = periods[0]
        is_near = (
            first["year"] == now.year and first["month"] <= now.month + 1
        ) or (
            first["year"] == now.year + 1 and now.month == 12 and first["month"] == 1
        )
        if is_near:
            queries.append(f"{destination} 天気予報 今週 来週")
            logger.info("Added weather forecast query (departure is near)")
    try:
        from tavily import TavilyClient
        tavily = TavilyClient(api_key=TAVILY_API_KEY)
        snippets = []
        for query in queries:
            logger.info(f"Tavily search: {query}")
            result = tavily.search(query=query, max_results=2, search_depth="basic")
            for item in result.get("results", []):
                title = item.get("title", "")
                content = item.get("content", "")
                url = item.get("url", "")
                if content:
                    snippets.append(f"【{title}】{content[:300]} (出典: {url})")
        if snippets:
            logger.info(f"Tavily search returned {len(snippets)} results total")
            return "\n\n".join(snippets)
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
                "id": "mofa_safety_check",
                "name": "外務省安全情報確認",
                "description": "外務省の海外安全情報をもとに渡航先の危険度レベルを返します。メッセージの先頭に「[外務省安全確認]」を付けて渡航先を送信してください。",
                "tags": ["safety", "mofa", "risk-level"],
                "examples": [
                    "[外務省安全確認] バンコク",
                    "[外務省安全確認] モスクワ",
                ],
            },
            {
                "id": "travel_report",
                "name": "渡航先情報レポート",
                "description": "渡航先・期間に基づき、治安・気候・注意事項・現地イベント等の実態情報を提供します。",
                "tags": ["travel", "report", "local-info"],
                "examples": [
                    "来月バンコクに1週間行きます。現地情報を教えてください。"
                ],
            },
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


@app.get("/debug-mofa")
async def debug_mofa(dest: str = "バンコク"):
    """外務省レベル取得のデバッグ用エンドポイント"""
    code = _get_mofa_country_code(dest)
    level = await asyncio.to_thread(get_mofa_safety_level, dest)
    return {"dest": dest, "code": code, "level": level}




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

        # 外務省安全確認モード：メッセージ先頭に "[外務省安全確認]" がある場合
        if user_text.strip().startswith("[外務省安全確認]"):
            destination = user_text.strip().removeprefix("[外務省安全確認]").strip()
            logger.info(f"MOFA safety check mode: destination={destination!r}")
            level_text = await asyncio.to_thread(get_mofa_safety_level, destination)
            if level_text and level_text != "情報なし":
                # シンプルな事実のみ返す（CTA はネイティブエージェント側に任せる）
                reply_text = (
                    f"外務省の海外安全情報（{destination}）：\n{level_text}"
                )
            else:
                reply_text = "外務省の安全情報を取得できませんでした。外務省海外安全ホームページ（https://www.anzen.mofa.go.jp/）でご確認のうえ、レベルをお知らせください。"
        else:
            # 通常モード：渡航レポート
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
