# watsonx Orchestrate 外部A2Aエージェント連携・検証作業報告レポート

## 1. 目的・背景

* **目的**：watsonx Orchestrate（wxO）に外部の独立したサービスとして動作する **A2A（Agent-to-Agent）エージェント** を接続し、接続試験およびデモンストレーションを構築・検証する。

* **連携方式**：AI Gateway ではなく、外部サービスとして機能を持つ A2A エージェント連携を採用。

* **デモ題材**：海外出張リスク分析をサンプルテーマとして採用し、渡航先・期間・目的などの入力に対し、治安・安全、健康・医療、入国/ビザ/法規/商習慣、出張者への推奨アクションを整理する。あわせて、向こう1週間の天気予報や渡航時期の祝日・大型イベント・行事・直近ニュースなどの最新情報を反映させるため、Google検索グラウンディングの活用を検証している。

## 2. システム構成・運用環境

追加費用を抑える方針で、以下の構成とした。

* **呼び出し元**：watsonx Orchestrate（wxO）から A2A RPC（`/` エンドポイントの `message/send` / `tasks/send`）経由でリクエストを送信。

* **エージェントサーバー**：FastAPI で実装した `agent_server.py`（`requirements.txt` とあわせて構成）。

* **ソース管理・ホスティング**：GitHub でソース管理を行い、Render の無料 Web Service 上で稼働（15分間の無通信でスリープし、初回復帰に30秒〜1分程度かかる特性あり）。

* **LLM／API基盤**：Google AI Studio で発行した Gemini API キーを利用（プロジェクト：`wxo-a2a` / 実ID：`gen-lang-client-0766291750`）。

## 3. 実装内容と設計のポイント

### プロンプト・指示制御（`SYSTEM_INSTRUCTION`）

* コード内の `DEFAULT_SYSTEM_INSTRUCTION` に基本となる指示文を保持しつつ、Render の環境変数 `SYSTEM_INSTRUCTION` が設定されている場合はそちらで上書きする設計としている。

* 本番／デモ用の詳細な指示内容は Render の環境変数側で定義して上書き運用する。

### モデル選定とフォールバック戦略

* 応答時間の遅延やタイムアウトを防ぐため、モデル候補は闇雲に増やさず、本命・次点・検索なしの最終保険の **3モデル構成** に絞っている。

  1. `PRIMARY_MODEL`（環境変数 `GEMINI_MODEL` があれば優先、デフォルトは `gemini-3.8-flash` / `search=True`）

  2. `gemini-3.7-flash`（`search=True`）

  3. `gemma-4-26b-a4b-it`（`search=False`）

* モデル名の前後空白除去、`models/` プレフィックスの正規化、重複排除を行い、存在しない／`404` になりやすいエイリアス（`gemini-flash-latest` 等）は除外している。

* `429 RESOURCE_EXHAUSTED` が発生した際は、同一モデルでの再試行をスキップして次の候補へ進む制御を入れ、無駄な待機やタイムアウトを抑えている。

### 検索グラウンディングと応答抽出処理

* **Google検索グラウンディング**：Gemini 系モデル（`gemini-*`）呼び出し時には `types.Tool(google_search=types.GoogleSearch())` を付与して検索あり（`search=True`）で実行し、Gemma 系（`gemma-*`）では検索ツールを付与せず（`search=False`）実行する。

* **空応答対策（`_extract_response_text`）**：HTTP `200 OK` で返却されたにもかかわらず `response.text` が空になるケースに備え、`candidates` 配下の `content.parts` を走査して `thought` パート以外のテキストを抽出する処理を実装している。異常時は `finish_reasons` を含めてログ出力し、原因を追いやすくしている。

### A2A エンドポイント・メタデータ

* エージェントカード配信用に `/.well-known/agent-card.json` および `/.well-known/agent.json` を提供し、リクエストの `x-forwarded-proto` や `host` からベースURLを動的に組み立てて返却する。

* 稼働確認用に `/health`、利用可能モデル確認用に `/models` を用意している。

* A2A RPC（`/`）では `message/send` および `tasks/send` を受け付け、ユーザー発話テキストを抽出して `call_gemini` を非同期スレッド（`asyncio.to_thread`）経由で実行し、タスク完了形式（`status.state: "completed"` および `artifacts`）でレスポンスを返却する。

## 4. 検証結果と現在の状況

### 確認できたこと

* watsonx Orchestrate から A2A 経由で Render 上の `agent_server.py` を呼び出し、wxO 画面上にエージェントからの応答が表示される一連の導線は動作を確認済み。

* `gemini-3.8-flash (search=True)` および `gemini-3.7-flash (search=True)` で `429 RESOURCE_EXHAUSTED` が発生した際、`gemma-4-26b-a4b-it (search=False)` へフォールバックして応答を返却できることを確認済み。

### 現在の課題

* 検索あり（`search=True`）の Gemini 系モデルが `429 RESOURCE_EXHAUSTED` で失敗し、検索なし（`search=False`）の `gemma-4-26b-a4b-it` にフォールバックしている状態のため、向こう1週間の天気予報や直近イベント・最新ニュースなどの最新情報が応答に反映されていない。

## 5. 今後の進め方

1. **クォータ・レート制限の確認**：Google AI Studio および対象プロジェクト側で、`gemini-3.8-flash` と `gemini-3.7-flash`（特に Google Search Grounding 併用時）のレート制限・利用枠の状況を確認する。

2. **検索あり呼び出しの安定化**：クォータ制限の解消や呼び出し条件の見直しを行い、`search=True` で `200 OK` が返る状態を作る。

3. **最新情報反映の検証**：Render の環境変数 `SYSTEM_INSTRUCTION` の指示どおり、直近の天気や行事・ニュースがレポートに含まれるか最終確認を行う。