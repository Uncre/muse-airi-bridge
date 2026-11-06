# 設計と調査根拠

## 1. モジュール構成と入出力の境界

各モジュールの責務を明確に分離し、外部仕様への依存箇所を限定しています。

- **`api`**: HTTP認証、リクエストサイズ制限、JSONおよびSSEレスポンスの組み立てを担当します。
- **`requests`**: AIRIからの呼び出し種別（通常会話、ローカル接続検証、拒否すべき補助呼び出し等）を判別し、装飾を除去したユーザー原文を抽出します。
- **`protocol`**: 不可視ヘッダーと感情フッターのビット単位の符号化・復号・解析を担当します。
- **`worker`**: 新規発言を1件ずつ直列に処理し、投稿前後の状態遷移と返信待ちポーリングを管理します。
- **`journal`**: 投稿前後のジョブ状態、ハッシュ、履歴位置、取得した返信をSQLiteへ永続化します。
- **`muse`**: `muse-cli` の内部API（`Gateway` やCookie読み込み）に依存する唯一のアダプターです。
- **`emotions`**: Museの感情コードをAIRIの `ACT` マーカーへ変換します。

### 送信データの最小化

- `system` / `developer` メッセージ、過去の会話履歴、ツール定義は「リクエスト種別の判別」と「同一リクエスト識別用のハッシュ計算」にのみ使用し、Museへは送信しません。
- 通常の会話ターンにのみASCII `AIRI1` の不可視符号（40文字）を先頭に付与し、送信ペイロードの `items[0].text` を「不可視ヘッダー＋無改変の抽出原文」とします。
- ローカル `ping` や拒否された補助呼び出しはMuseへ一切送信しません。
- セッションIDは指定せず、常にメインチャットへ接続します。メモリーやソウルの自動編集も行いません。

---

## 2. 不可視プロトコル（0.2.0）

### 符号化形式

- `U+200B = 0`（ZERO WIDTH SPACE）、`U+200C = 1`（ZERO WIDTH NON-JOINER）として、各バイトを上位ビット（MSB）から8文字へ展開します。
- **入力ヘッダー**: ASCII `AIRI1`（5バイト＝40文字）
- **返信フッター**: ASCII `AE1:`＋感情コード1文字（5バイト＝40文字）
- いずれも表示用の囲み文字や区切り文字は一切付けません。

### `payload.content` 基準の厳格な照合

- 新方式（`invisible-v1`）のジョブでは、`payload.content` を送信照合および返信本文・フッター取得の共通基準とします。
- 表示用フィールド（`display_text`）と内容が異なる場合でも `payload.content` を採用し、`payload.content` 自体が存在しない場合は推測せずエラー（`muse_content_missing`）とします。
- 未確認の表示変換を仮定してヘッダーを取り除いたり、別フィールドから符号を補完したりすることはありません。

### フッター解析と安全側のフォールバック

- 解析対象は返信文字列の**絶対末尾にある専用40文字のみ**です。
- 正しい固定署名（`AE1:` の32文字）を持つ末尾フッターは本文から除去します。
  - 未知の感情コードや破損コードはフッターを除去したうえで `neutral` とします。
  - 末尾に専用フッターが連続して2個以上付いている場合は、すべて除去して `neutral` とします。
- 署名自体の破損、途中で切れた符号、末尾に空白や改行などが付いた符号はフッターと断定せず、文字列をそのまま保って `neutral` とします。
- 本文内や引用内の不可視文字を一括削除することはありません。また、絵文字からの感情推定、修復用のLLM呼び出し、再投稿も行いません。

### ソウル規約の適用範囲

- 適用条件と9種類の感情コードを定義した [ソウル追記用プロンプト](muse-soul-addendum.md) は、ユーザーが自身のMuseのソウルへコピーして使用します。
- 規約は「現在回答する最新ユーザー発言の先頭ヘッダー」のみで発動し、過去の履歴、通常のMuse UIからの発言、自発通知には持ち越さないよう明記しています（なお、Museが継続的に規約へ従う成功率は実運用での確認事項です）。

---

## 3. 調査・確認した公開コード

2026-11-06時点で、導入済みWindows版 `muse-cli 0.3.10` の実装と以下の公開コード・仕様を確認しました。

- **muse-cli (`main`: `2e42aeccbef71437f4ccd4faa626414f2461be7b`)**
  - [cli.py](https://github.com/nikships/muse-cli/blob/2e42aeccbef71437f4ccd4faa626414f2461be7b/src/muse_cli/cli.py): 設定読み込み、投稿、履歴取得、完成返信の判定条件。
  - [gateway.py](https://github.com/nikships/muse-cli/blob/2e42aeccbef71437f4ccd4faa626414f2461be7b/src/muse_cli/gateway.py): 同期 `Gateway`、Noise通信、WebSocket受信の所有権。
  - [PROTOCOL.md](https://github.com/nikships/muse-cli/blob/2e42aeccbef71437f4ccd4faa626414f2461be7b/docs/PROTOCOL.md): 投稿ストリーム（`chat.stream`）は送信echoで終了し、履歴（`chat.history`）が返信判定の基準であること。背景通知は `reply_to_message_id` で区別されること。
- **AIRI (`v0.14.1`: `349ecfeda2d0e1508a6b2c2a25740757c9ed51c6`)**
  - [chat-orchestrator-runtime.ts](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/core-agent/src/runtime/chat-orchestrator-runtime.ts): 日時接頭辞、返信先、Notice、スキル指示、Contextの入力組み立て。
  - [render-context.ts](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/core-agent/src/messages/render-context.ts): `\n[Context]\n` セクションの描画形式。
  - [prompt-recipe.ts](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/stage-ui/src/stores/chat/prompt-recipe.ts): 通常チャットのシステムプロンプト形式規則、背景レシピの識別文。
  - [openai-compatible.ts](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/provider-inference/src/validators/openai-compatible.ts): 単独 `ping`（`max_tokens=16`）による接続検証。
  - [queues.ts](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/stage-ui/src/composables/queues.ts): `<|ACT ...|>` マーカーの `emotion.name` / `emotion.intensity` 解析仕様。
  - [artistry-autonomous.ts](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/stage-ui/src/stores/modules/artistry-autonomous.ts): Cinematic Directorによる自律画像生成の補助推論。
  - [spark-notify/agent.ts](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/core-agent/src/agents/spark-notify/agent.ts): Spark Notifyによる外部通知用の別リクエスト。
- **OpenAI仕様**
  - [OpenAI Chat Completions ストリーミング仕様](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)

調査時点のAIRI `main`（`11b8989f6dd7f840cf6b15ce59e367656fb11ac6`）でも主要な装飾形式を照合しました。また、既存の認証情報を用いて実際のMuse履歴のフィールド名・状態を読み取り、`completed` 状態と背景通知の判別項目を確認しています（認証値や会話本文は調査出力に含めていません）。

---

## 4. 障害時の扱いとジャーナル設計

### 状態遷移と二重送信防止

- ジョブの状態は `reserved → prepared → sending → waiting → completed` の順に遷移します。
- ネットワーク投稿を行う直前に `sending` をSQLiteへコミットします。
  - 投稿前の失敗は `failed` となり、安全に再試行できます。
  - 投稿後（`sending` 以降）の失敗やタイムアウトは `uncertain`（保留）となり、解消されるまで新規投稿を止めます。
  - プロセス再起動時も、中断された `sending` / `waiting` ジョブは `uncertain` へ移行し、自動再投稿は行いません。
- SQLiteには「原文のハッシュ」「実際の送信文字列（wire text）のハッシュ」「プロトコル方式」「送信前の履歴シーケンス番号（`baseline`）」「符号付きの返信本文」を保存します。AIRIのリクエストには発言IDがないため、`messages` と `model` の正規化JSONハッシュを同一リクエストの識別子としています。厳密なexactly-once保証ではなく、「送信成否が不明なリクエストを自動再試行しない」安全設計です。

### スキーマ移行（`PRAGMA user_version=2`）

- 旧DB（`0.1.0`）を検出した場合は、SQLiteのバックアップAPIで `journal.pre-v2.sqlite3` を作成してから、トランザクション内で `protocol` 列と `wire_hash` 列を追加します。
- 旧行の `protocol` は `raw-v0`、`wire_hash` は旧 `text_hash` に固定し、新方式へ推測変換することはしません。新しく作成されるジョブのみが `invisible-v1` になります。未知のスキーマバージョンや未知のプロトコルが含まれる場合は起動を拒否します（fail-closed）。
- 旧ジョブの扱い:
  - 旧 `reserved` / `prepared` を含むすべての旧保留ジョブは `uncertain` として保持し、自動投稿しません。
  - 旧完了キャッシュは有効期限内のみ `neutral` で再生し、フッター解析は行いません。
  - 期限切れの旧完了キャッシュ、旧失敗ジョブ、明示解除済みの旧ジョブは、返信本文を消去したうえで再投稿防止の記録（tombstone）を残します。新しい会話ターンは別リクエストとして通常どおり進行できます。
  - 旧保留ジョブの `reconcile` のみ、旧版（`0.1.0`）と同じ表示用フィールド優先順位とハッシュで照合します。

### エラー情報の秘匿とSSEの安全性

- `muse-cli` の `Gateway` が投げる例外文字列には認証トークン付きURLや上流レスポンスが含まれ得るため、例外の原文や例外チェーン（`__cause__`）は公開HTTPエラーにも通常ログにも一切出力しません。
- ストリーミング（SSE）要求時も、Museからの完成返信が確定してからレスポンスを開始するため、タイムアウトや上流エラーが「正常な空の発話ストリーム」として扱われることはありません。
