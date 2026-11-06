# 設計と調査根拠

## 入出力の境界

`api`はHTTP認証・入力サイズ・JSON/SSEを扱い、`requests`がAIRIの呼び出しの種類を判別して原文を抽出します。`protocol`が不可視ヘッダーと感情フッターを符号化・解析し、`worker`が新規発言を直列に扱い、`journal`へ投稿前後の状態を記録します。`muse`だけがmuse-cliの内部APIに依存します。`emotions`はMuseの感情コードをAIRIのACTへ変換します。

system／developer、過去の会話、ツール定義は判別とハッシュ計算だけに使います。通常会話だけにASCII `AIRI1`の不可視符号40文字を付け、`items[0].text`を「ヘッダー＋無改変の抽出原文」にします。ローカルpingや拒否した補助呼び出しはMuseへ送信しません。セッションIDを指定せずメインチャットへ接続します。メモリーやソウルの自動編集は行いません。

## 不可視プロトコル（0.2.0）

`U+200B=0`、`U+200C=1`として、各バイトを上位ビットから8文字へ展開します。入力はASCII `AIRI1`、返信末尾はASCII `AE1:`＋感情コード1文字です。両方40文字で、表示用の囲みや区切りは付けません。

新ジョブは`payload.content`を送信照合と返信本文・フッター取得の共通の基準にします。表示用フィールドと異なる場合もcontentを採用し、content自体がなければエラーとします。確認できていない表示変換を仮定してヘッダーを取り除いたり、別フィールドから符号を補ったりしません。

解析するのは絶対末尾の専用40文字だけです。正しい固定署名を持つフッターは除去します。未知コード・破損コードはneutral、連続する二重以上の専用フッターは全て除去してneutralです。署名の破損・途中で切れた符号・末尾に空白等が付いた符号はフッターと断定せず、文字列をそのまま保ってneutralとします。本文内・引用内の不可視文字を一括削除しません。絵文字からの推定、修復用LLM呼び出し、再投稿はありません。

適用条件と9種類の感情コードを含む[ソウル追記用プロンプト](muse-soul-addendum.md)はユーザーがコピーして使います。規約は現在回答するユーザー発言の先頭ヘッダーだけで発動し、過去の履歴や通常のMuse UI発言、自発通知には持ち越さないよう記載しています。Museが規約に従う継続的な成功率は未検証です。

## 確認したコード

2026-11-06、導入済みWindows版muse-cli 0.3.10と以下の公開コードを確認しました。

- muse-cli main: `2e42aeccbef71437f4ccd4faa626414f2461be7b`
  - [cli.py](https://github.com/nikships/muse-cli/blob/2e42aeccbef71437f4ccd4faa626414f2461be7b/src/muse_cli/cli.py): 設定、投稿、履歴、完成返信判定。
  - [gateway.py](https://github.com/nikships/muse-cli/blob/2e42aeccbef71437f4ccd4faa626414f2461be7b/src/muse_cli/gateway.py): 同期Gateway、Noise通信、受信所有権。
  - [PROTOCOL.md](https://github.com/nikships/muse-cli/blob/2e42aeccbef71437f4ccd4faa626414f2461be7b/docs/PROTOCOL.md): 投稿ストリームは送信echoで終了。履歴が返信判定の基準。背景通知はreply_to_message_idで区別。
- AIRI v0.14.1: `349ecfeda2d0e1508a6b2c2a25740757c9ed51c6`
  - [入力組み立て](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/core-agent/src/runtime/chat-orchestrator-runtime.ts): 日時、返信先、Notice、スキル指示、Context。
  - [コンテキスト描画](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/core-agent/src/messages/render-context.ts): `\n[Context]\n`。
  - [会話プロンプト](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/stage-ui/src/stores/chat/prompt-recipe.ts): 通常チャットの形式規則、背景レシピの識別文。
  - [接続検証](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/provider-inference/src/validators/openai-compatible.ts): 単独ping、max_tokens=16。
  - [感情キュー](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/stage-ui/src/composables/queues.ts): ACTのemotion/name/intensity。
  - [自律画像生成](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/stage-ui/src/stores/modules/artistry-autonomous.ts): Cinematic Directorによる補助推論。
  - [Spark Notify](https://github.com/moeru-ai/airi/blob/349ecfeda2d0e1508a6b2c2a25740757c9ed51c6/packages/core-agent/src/agents/spark-notify/agent.ts): 外部通知の別リクエスト。
- [OpenAI Chat Completionsストリーミング仕様](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)

調査時のAIRI main `11b8989f6dd7f840cf6b15ce59e367656fb11ac6`でも主要な装飾形式を照合しました。既存認証でMuse履歴のフィールド名・状態を読み取り、completedと背景通知の判別項目を確認しました。認証値・会話本文は調査出力に含めていません。

## 障害時の扱い

状態は`reserved → prepared → sending → waiting → completed`です。ネットワーク投稿の直前にsendingを永続化します。投稿前の失敗はfailed、投稿後の失敗はuncertainとして、新たな投稿を止めます。再起動時もsending/waitingはuncertainへ移し、自動再投稿しません。

SQLiteに原文のハッシュ、実際のwire textのハッシュ、プロトコル方式、送信前seq、符号付きの返信本文を保存します。AIRIに発言IDがないため、messagesとmodelのハッシュを同一リクエストの識別子にしています。厳密なexactly-once保証ではなく、不明な送信を自動再試行しない設計です。

スキーマは`PRAGMA user_version=2`です。旧DBはSQLite backup APIで`journal.pre-v2.sqlite3`を作成してから、トランザクション内で列を追加します。旧行の`protocol`は`raw-v0`、`wire_hash`は旧`text_hash`に固定し、新方式へ推測変換しません。新しいジョブだけが`invisible-v1`です。未知のスキーマ版・プロトコルがあれば起動を拒否します。

旧reserved/preparedも含む全ての旧保留ジョブをuncertainとして保持し、自動投稿しません。旧完了キャッシュは有効期限内だけneutralで再生し、フッター解析はしません。期限切れ旧完了・旧失敗・明示解除済みの旧識別子は、返信本文を保持せず再投稿防止の記録を残します。新しい会話ターンは別リクエストとして進行できます。旧保留ジョブのreconcileだけは、旧版の表示用フィールド優先順位とハッシュで照合します。

Gatewayの例外文字列には認証URLや上流データが含まれ得るため、その文字列は公開エラーにも通常ログにも出しません。SSEの開始前に返信を確定するため、失敗を正常な空回答として扱いません。
