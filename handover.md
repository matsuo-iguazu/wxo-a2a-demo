# 引継ぎドキュメント — wxo-a2a-demo

> `wxo_a2a.md` はプロジェクトの背景・目的を記載した原本です。変更不要（Readonly）。
> このファイル（`handover.md`）が作業の現在地と引継ぎ情報を管理します。

---

## ⚠️ wxO 環境操作ルール（必読・厳守）

**対象リソース以外には絶対にアクセス・更新・削除しないこと。本番稼働しているリソースが同一環境にある。**

| 種別 | 対象リソース名 | 備考 |
|---|---|---|
| ネイティブエージェント | `Untitled_Agent_1_0061NH`（表示名：`M_test_A2A_gemini`） | このプロジェクト専用 |
| 外部エージェント | `agent_Gemini_mofa_check`、`agent_Gemini_travel_report` | このプロジェクト専用 |
| ナレッジベース | `m_test_A2A_kb` | このプロジェクト専用 |

### 認証について
- wxO SaaS 環境への認証は**人間が行う**（Bob/AIは認証操作を行わない）
- 認証セッションは**約1時間で切れる**
- 操作前に認証が有効かどうかを確認してから進めること

### ナレッジベースのベクトルLLM設定
- KBのベクトルLLMは**デフォルトではなく `multilingual` のものを使用**すること

---

## 0. デモシナリオ（確定版）

### コンセプト

> 「某社が社内で運用している**渡航情報調査ツール**（Gemini on Render）があります。wxOと連携することで、そのツールの結果を使って**社内規程の照合・申請書作成まで自動でサポート**できるようになります。」

| 役割 | 担当 | 備考 |
|---|---|---|
| 外務省リアルタイム危険度レベル取得 | Gemini A2A（`/mofa-check`） | 外務省オープンデータXML直接取得 |
| 渡航先の現地情報（治安・気候等） | Gemini A2A（`/travel-report`） | Tavily + Gemini |
| 社内規程照合・申請書ドラフト | wxO ネイティブエージェント + KB | KB: `m_test_A2A_kb` |

### デモフロー（動作確認済み ✅ 2026-11-25）

```
ユーザー：「来月バンコクに出張することになりました」
  ↓
wxO → agent_Gemini_mofa_check（POST /mofa-check）に転送
  ↓
Gemini：「外務省の海外安全情報によると、バンコクの危険度は
         「レベル3（渡航中止勧告）」です。
         社内規程に照らし合わせて、渡航可否をご確認ください。」
  ↓ ← ここでwxOのコンテキストにレベル情報が残る（重要）
ユーザー：「社内規程を確認してください」
  ↓
wxO → KB（m_test_A2A_kb）を参照して規程照合
  ↓
wxO：「社内規程により、バンコク（危険度レベル3）への渡航は原則禁止です...」

--- シンガポールの場合 ---

ユーザー：「じゃあシンガポールはどうですか？」
  ↓
Gemini：「外務省によると、シンガポールの危険度は「危険情報なし（概ね安全）」です。
         社内規程に照らし合わせて...」
  ↓
ユーザー：「規程確認して」
  ↓
wxO：「問題ありません。やることリスト...申請書を作成しますか？」
  ↓
申請書ドラフト生成 ✅
```

### 重要な技術的発見（2026-11-25）

**「A2A転送」でもwxOのコンテキストは引き継がれる。**

外部エージェントの応答は「転送」としてユーザーに直接表示されるが、その内容はwxOのコンテキストに残り、次のターンでネイティブエージェントが参照できる。

---

## 1. プロジェクトの目的（要約）

watsonx Orchestrate（wxO）から外部 A2A エージェントを接続するデモを構築する。
デモテーマは **海外出張支援**。既存の社内渡航情報調査ツール（Gemini）をwxOに接続し、規程照合・申請書作成まで一貫してサポートする体験を示す。

---

## 2. システム構成

| 要素 | 内容 |
|---|---|
| 呼び出し元 | watsonx Orchestrate（A2A RPC） |
| エージェントサーバー | FastAPI (`agent_server.py`) |
| ホスティング | Render 無料 Web Service（15分無通信でスリープ、復帰に30秒〜1分） |
| ソース管理 | GitHub `matsuo-iguazu/wxo-a2a-demo` |
| Web 検索 | Tavily Search API（最新情報取得） |
| LLM | Gemini API（Google AI Studio、プロジェクト `wxo-a2a`） |
| デプロイ | GitHub push → Render 自動デプロイ |

### watsonx Orchestrate 側の設定

| 項目 | 値 |
|---|---|
| ネイティブエージェント内部名 | `Untitled_Agent_1_0061NH` |
| ネイティブエージェント表示名 | `M_test_A2A_gemini` |
| ネイティブエージェント ID | `11c04a11-f68f-4ac9-9d48-6d5c4364513d` |
| LLM | `bedrock/us.anthropic.claude-sonnet-4-6` |
| collaborators | `agent_Gemini_mofa_check`、`agent_Gemini_travel_report` |
| knowledge_base | `m_test_A2A_kb` |
| sync_tool_flow_interactions | `true` |

| 外部エージェント名 | api_url | 用途 |
|---|---|---|
| `agent_Gemini_mofa_check` | `https://wxo-a2a-demo.onrender.com/mofa-check` | 外務省危険度レベル取得 |
| `agent_Gemini_travel_report` | `https://wxo-a2a-demo.onrender.com/travel-report` | 渡航情報レポート |

> **認証**：Bearer Token（token: `dummy`）/ Render側では認証検証なし（実質素通り）

### Renderエンドポイント一覧

| パス | 用途 |
|---|---|
| `POST /mofa-check` | 外務省危険度レベル取得専用（skill: mofa_safety_check） |
| `POST /travel-report` | 渡航情報レポート専用（skill: travel_report） |
| `POST /` | 後方互換（`[外務省安全確認]`プレフィックスで自動振り分け） |
| `GET /health` | 稼働確認 |
| `GET /models` | 利用可能モデル一覧 |
| `GET /.well-known/agent.json` | A2A エージェントカード |
| `GET /.well-known/agent-card.json` | 同上（別名） |
| `GET /debug-mofa` | 外務省API動作確認用（`?dest=バンコク`） |

---

## 3. 技術的制約（確定）

### A2A コラボレーター「転送」問題

**現象**：ネイティブエージェントがA2A外部エージェントをコラボレーターとして呼ぶと、外部エージェントの応答がネイティブエージェントのコンテキストを通らずユーザーに直接表示される（「転送」動作）。

**試したこと（全て効果なし）**：
- `sync_tool_flow_interactions: true/false` の切り替え → 変化なし
- Flow の Agent Node として外部エージェントを使う → UIに外部エージェントが出てこない（非サポート）
- instructions に「結果を受け取って整形せよ」と指示 → 転送なので受け取れない

**重要な発見**：転送でも**wxOのコンテキストには残る**。次のターンでネイティブエージェントが参照できる。

**現在の対応**：Render側（`/mofa-check`）の返答文を整形済みにする。

**サポートへ問い合わせ中**（2026-11-25）：
> "Native agent cannot post-process A2A collaborator response before displaying to user — is this expected behavior?"
> 確認項目：① 仕様か否か ② 回避策はあるか ③ Ideasに投稿すべきか

### バンコクがレベル3と返る件

外務省XMLは**国単位**でデータ取得。タイ国内の南部（マレーシア国境付近）がレベル3のため、バンコクと入力しても「タイの最高危険度 = レベル3」が返る。デモでは**モスクワ（レベル4）・シンガポール（危険なし）**を使うと正確。

---

## 4. 現在のコード状態

### 処理フロー（`/mofa-check`）

```
渡航先名を受け取る
  ↓ _resolve_mofa_code()  ← Gemini で都市名→国名変換 → キャッシュで国コード取得
外務省オープンデータ XML 取得（https://www.ezairyu.mofa.go.jp/opendata/country/{code}.xml）
  ↓
危険度レベル（riskLevel1〜4）を判定
  ↓
「外務省の海外安全情報によると、{destination}の危険度は「{level_text}」です。社内規程に照らし合わせて、渡航可否をご確認ください。」を返す
```

### 処理フロー（`/travel-report`）

```
ユーザーテキストを受け取る
  ↓ extract_travel_info()  ← Gemini で渡航先・期間をJSON解析
  ↓ search_travel_context()  ← Tavily で3〜4クエリ検索
  ↓ build_prompt_with_context()  ← 検索結果をプロンプトに付加
  ↓ call_gemini()  ← 最終回答生成
A2A レスポンスで返す
```

### モデル優先順位

```python
PRIMARY_MODEL = "gemini-3.1-flash-lite"
PREFERRED_MODELS = [
    "gemini-3.1-flash-lite",  # プライマリ ✅
    "gemma-4-26b-a4b-it",     # 最終保険
]
```

### 環境変数（Render）

| 変数名 | 状態 | 説明 |
|---|---|---|
| `GEMINI_API_KEY` | ✅ 設定済み | Gemini API キー |
| `TAVILY_API_KEY` | ✅ 設定済み | Tavily Search API キー |
| `SYSTEM_INSTRUCTION` | ✅ 設定済み | カスタムシステムプロンプト |

---

## 5. ナレッジベース

| 項目 | 値 |
|---|---|
| KB名 | `m_test_A2A_kb` |
| KB ID | `0a53f0e9-8c41-4980-9037-66f4732db17b` |
| 埋め込みモデル | `ibm/granite-embedding-278m-multilingual` |
| ドキュメント | `travel_policy.txt`（1件、6.7 KB）|
| インデックス状態 | `ready` |

| ファイル | 内容 |
|---|---|
| `knowledge-bases/travel_policy.md` | 出張規程サンプル（表示用・ソース管理用） |
| `knowledge-bases/travel_policy.txt` | KB 登録用（.md は非サポートのため .txt でコピー） |
| `knowledge-bases/m_test_A2A_kb_nodocs.yaml` | KB スペックファイル（相対パス版・実際に使用） |

---

## 6. 次回やること

| 優先度 | タスク | 備考 |
|---|---|---|
| 高 | サポート回答待ち | A2A転送問題の仕様確認 |
| 高 | サポート回答に応じてIdeas投稿 | 投稿文案は別チャットで作成済み |
| 中 | アーキテクチャー図の作成 | デモ説明用（Mermaid or 画像） |
| 中 | デモビデオの撮影 | シナリオ：シンガポール→規程OK→申請書 / モスクワ→規程NG |
| 中 | バンコクの国単位レベル問題の対応 | 都市単位のレベル取得を検討 |
| 低 | wxO Suggested Replies でボタン表示 | 未着手 |

---

## 7. コミット履歴（主要）

| コミット | 内容 |
|---|---|
| `dfe971e` | /mofa-check 返答に社内規程照合を促す文言を追加 |
| `e806576` | /mofa-check 返答を自然な日本語文体に変更 |
| `c7ccfb6` | エンドポイント分離（/mofa-check・/travel-report）・agent card skill に url 追加 |
| `2166822` | 静的辞書廃止 → 全地域XMLキャッシュ＋Gemini国名変換に移行 |
| `c7450fb` | 外務省オープンデータXML APIに切り替え |

---

## 8. 参考リンク

- Render URL：https://wxo-a2a-demo.onrender.com
- GitHub：https://github.com/matsuo-iguazu/wxo-a2a-demo
- Render ダッシュボード：https://dashboard.render.com
- クォータ確認：https://ai.dev/rate-limit
- Tavily：https://app.tavily.com
- 外務省海外安全ホームページ：https://www.anzen.mofa.go.jp/
- 外務省オープンデータ：https://www.ezairyu.mofa.go.jp/opendata/

---

## 9. クォータリセットポリシー（Google AI Studio 無料枠）

| 種別 | リセット間隔 |
|---|---|
| **RPM**（1分あたり） | 1分ごと（ローリングウィンドウ） |
| **RPD**（1日あたり） | 過去28日間ローリングウィンドウ |
