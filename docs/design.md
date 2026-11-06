# 設計と調査根拠

## 入出力の境界

`api`はHTTP認証・入力サイズ・JSON/SSEを扱い、`requests`がAIRIの呼び出しの種類を判別して原文を抽出します。`worker`は新規発言を直列に扱い、`journal`へ投稿前後の状態を記録します。`muse`だけがmuse-cliの内部APIに依存します。`emotions`は完成返信をAIRI向けに装飾します。

system／developer、過去の会話、ツール定義は判別とハッシュ計算だけに使います。Museの送信bodyには`items[0].text`として原文だけを設定します。セッションIDを指定せずメインチャットへ接続します。メモリーのコピー・編集は行いません。

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

SQLiteに原文のハッシュ、送信前seq、返信本文を保存します。AIRIに発言IDがないため、messagesとmodelのハッシュを同一リクエストの識別子にしています。厳密なexactly-once保証ではなく、不明な送信を自動再試行しない設計です。

Gatewayの例外文字列には認証URLや上流データが含まれ得るため、その文字列は公開エラーにも通常ログにも出しません。SSEの開始前に返信を確定するため、失敗を正常な空回答として扱いません。
