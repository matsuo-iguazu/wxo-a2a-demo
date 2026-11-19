# 引継ぎドキュメント — wxo-a2a-demo

> `wxo_a2a.md` はプロジェクトの背景・目的を記載した原本です。変更不要（Readonly）。
> このファイル（`handover.md`）が作業の現在地と引継ぎ情報を管理します。

---

## 1. プロジェクトの目的（要約）

watsonx Orchestrate（wxO）から外部 A2A エージェントを接続するデモを構築する。  
デモテーマは **海外出張リスク分析**。モデルの静的な知識だけでなく、**Google 検索グラウンディング**を使って天気・通貨・現地イベント・最新ニュースなどのリアルタイム情報を応答に含めることを目標としている。

**方針の補足（オーナーより）**：  
- クォータを消費するような高負荷な使い方は不要。デモとして動けばよい。
- 「外部エージェントとしての付加価値」を示すため、wxO 単体では得られない最新情報（天気・祝日・現地イベント等）を含めることが重要。

---

## 2. システム構成

| 要素 | 内容 |
|---|---|
| 呼び出し元 | watsonx Orchestrate（A2A RPC） |
| エージェントサーバー | FastAPI (`agent_server.py`) |
| ホスティング | Render 無料 Web Service（15分無通信でスリープ、復帰に30秒〜1分） |
| ソース管理 | GitHub `matsuo-iguazu/wxo-a2a-demo` |
| LLM | Gemini API（Google AI Studio、プロジェクト `wxo-a2a`） |
| デプロイ | GitHub push → Render 自動デプロイ |

**エンドポイント一覧**：

| パス | 用途 |
|---|---|
| `POST /` | A2A RPC（`message/send` / `tasks/send`） |
| `GET /health` | 稼働確認・設定確認 |
| `GET /models` | 利用可能モデル一覧（Gemini API に実際に問い合わせ） |
| `GET /.well-known/agent.json` | A2A エージェントカード |
| `GET /.well-known/agent-card.json` | 同上（別名） |

---

## 3. 本日（2026-11-19）の作業内容と発見事項

### 3-1. 動作確認

- `GET /health`、`GET /models`、`POST /`（A2A リクエスト）すべて正常に応答することを確認済み。
- PowerShell の `Invoke-RestMethod` で日本語レスポンスが文字化けするのは **PowerShell コンソールのエンコーディング問題**であり、API レスポンス自体は正常な UTF-8。

### 3-2. `GET /models` で確認した利用可能モデル（`wxo-a2a` プロジェクト）

```
gemini-2.5-flash, gemini-2.5-pro, gemma-4-26b-a4b-it, gemma-4-31b-it,
gemini-2.5-flash-lite, gemini-3.1-flash-lite, gemini-3.5-flash,
gemini-3.6-flash, gemini-3.7-flash, gemini-3.8-flash,
gemini-4-argon, gemini-4-carbon, gemini-4-hydrogen, gemini-4-neon,
antigravity-preview-*, deep-research-*, gemini-embedding-* など
```

> **注意**：`gemini-2.5-flash` は一覧に出るが、実際に呼び出すと `404 NOT_FOUND`（新規ユーザーには提供終了）。
> エラーメッセージ：`"This model models/gemini-2.5-flash is no longer available to new users."`

### 3-3. クォータ状況（Google AI Studio / `wxo-a2a` プロジェクト、無料枠）

確認 URL：https://ai.dev/rate-limit

| モデル | RPM上限 | RPD上限 | 備考 |
|---|---|---|---|
| Gemini 3.8 Flash | 5 | 20 | 本日 11/20 消費済み |
| Gemini 3.6 Flash | 5 | 20 | |
| **Gemini 3.1 Flash Lite** | **15** | **500** | **最も余裕あり ← 現プライマリ** |
| Gemma 4 26B | 30 | 14,400 | |
| Gemma 4 31B | 30 | 14,400 | |

> **重要**：Gemini 系モデルはすべて**同じプロジェクト単位のクォータ**を共有している可能性が高い。
> 本日の検証でクォータを使い切り、全 Gemini モデルが `429 RESOURCE_EXHAUSTED` になった。
> RPM は1分でリセット。RPD は太平洋時間深夜0時（日本時間 午後4〜5時頃）にリセット。

### 3-4. エラーパターン

| エラー | 原因 | 対処 |
|---|---|---|
| `429 RESOURCE_EXHAUSTED` | RPM または RPD のクォータ超過 | 時間をおく。現コードは即次モデルへスキップ |
| `404 NOT_FOUND` | モデルが新規ユーザー向けに提供終了 | `gemini-2.5-flash` をリストから除外済み |
| `503 UNAVAILABLE` | Google 側の一時的な高負荷 | 3秒待ってリトライするコードを追加済み |

---

## 4. 現在のコード状態

### モデル優先順位（`agent_server.py` の `PREFERRED_MODELS`）

```python
PRIMARY_MODEL = "gemini-3.1-flash-lite"  # 環境変数 GEMINI_MODEL で上書き可

PREFERRED_MODELS = [
    "gemini-3.1-flash-lite",  # RPM=15, RPD=500 ← 最も余裕
    "gemini-3.8-flash",       # RPM=5,  RPD=20
    "gemini-3.6-flash",       # RPM=5,  RPD=20
    "gemma-4-26b-a4b-it",     # RPM=30, RPD=14,400（search=False、最終保険）
]
```

### フォールバック戦略

1. `gemini-*` → `search=True` で試行 → 失敗したら `search=False` で再試行
2. `429` が来たら同モデルの再試行をスキップして次候補へ
3. `503` が来たら3秒待って同一構成で1回リトライ → 失敗したら次候補へ
4. `gemma-*` → `search=False` のみ（Google Search Grounding 非対応）

### 環境変数（Render）

| 変数名 | 説明 |
|---|---|
| `GEMINI_API_KEY` | Gemini API キー（設定済み） |
| `GEMINI_MODEL` | プライマリモデル名の上書き（現在未設定 → コードデフォルト使用） |
| `SYSTEM_INSTRUCTION` | カスタムシステムプロンプト（設定済み・詳細な指示内容） |

---

## 5. 残課題・次のアクション

### 優先度 高
- [ ] **動作確認**：クォータリセット後（RPD は日本時間 午後4〜5時以降）に `POST /` を叩いて、`gemini-3.1-flash-lite (search=True)` で成功するか確認する
- [ ] **Search Grounding の有効性確認**：応答に天気・最新ニュース・現地イベントが含まれているかを確認する

### 優先度 中
- [ ] **wxO からのエンドツーエンド確認**：watsonx Orchestrate からの呼び出しで検索グラウンディング付き応答が表示されるか確認する
- [ ] **`wxo_a2a.md` の「今後の進め方」を更新**：クォータ問題の調査は完了したため、次フェーズの内容に書き換える

### 参考
- Render URL：https://wxo-a2a-demo.onrender.com
- Render ダッシュボード：https://dashboard.render.com（→ 下記アクセス手順参照）
- GitHub：https://github.com/matsuo-iguazu/wxo-a2a-demo
- クォータ確認：https://ai.dev/rate-limit
- Google AI Studio レート制限ドキュメント：https://aistudio.google.com/docs/rate-limits

---

## 7. Render ダッシュボードへのアクセス手順

1. https://dashboard.render.com を開く
2. **「GitHub でログイン」** を選択（GitHub アカウントで認証）
3. 左メニューまたはトップページのサービス一覧から **`wxo-a2a-demo`** を選択
4. 主要タブの使い方：

| タブ | 用途 |
|---|---|
| **Logs** | リアルタイムログ確認（モデルの成功/失敗、エラー内容） |
| **Environment** | 環境変数の確認・変更（`GEMINI_API_KEY`, `SYSTEM_INSTRUCTION` など） |
| **Events** | デプロイ履歴の確認 |
| **Settings** | サービス設定（GitHub リポジトリ連携、自動デプロイ設定など） |

> **Tips**：GitHub に push すると自動デプロイが走る。Logs タブで `==> Your service is live 🎉` が出ればデプロイ完了。

---

## 6. クォータリセットポリシー（Google AI Studio 無料枠）

| 種別 | リセット間隔 | 詳細 |
|---|---|---|
| **RPM**（1分あたり） | **1分ごと**（ローリングウィンドウ） | 1分待てば即回復 |
| **RPD**（1日あたり） | **太平洋時間（PT）深夜0時** | 日本時間 午後4時（PDT/夏時間）または午後5時（PST/冬時間） |

- 11月は PST（冬時間）のため、**日本時間 午後5時頃**に RPD がリセットされる。
- `429 RESOURCE_EXHAUSTED` が出ても、RPM 超過であれば**1〜2分待つだけで回復**する。
- RPD 上限に達した場合のみ、当日の午後5時まで待つ必要がある。
- `gemini-3.1-flash-lite` は RPD=500 と余裕があるため、RPD 上限に達しにくい。
