# muse-airi-bridge

AIRIのOpenAI Compatibleプロバイダーから、普段使っているMuseのメインチャットへ接続するPythonプロジェクトです。AIRI本体の変更は不要です。

**Museへ投稿するのは入力原文だけです。** AIRIが付ける日時・返信先・スキル指示・Contextを除去し、人格プロンプトや過去の会話は送りません。Muse側の人格・メモリー・標準ツールを使います。

## 対応環境

- Windows、Python 3.10以上、Git、uv。
- `muse-cli==0.3.10`。uvの仮想環境にも固定版をインストールします。既存のグローバル環境は変更しません。
- AIRI **v0.14.1**のリクエスト形式を対象にしています。調査時のmainでも主要な装飾は一致しましたが、他の版は未保証です。
- 既存の `~/.config/muse-cli/config.json` と `cookies.txt` を読み取り専用で利用します。

## インストールと起動（PowerShell）

このREADMEのあるディレクトリで実行します。

```powershell
uv sync --locked
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    $bridgeKey = python -c "import secrets; print(secrets.token_urlsafe(32))"
    (Get-Content .env -Raw).Replace('replace-with-a-long-random-local-key', $bridgeKey) | Set-Content .env -Encoding utf8
}
uv run --env-file .env muse-airi-bridge doctor
uv run --env-file .env muse-airi-bridge serve
```

`.env`の`MUSE_BRIDGE_API_KEY`をAIRIにも設定します。これはローカルラッパーの認証キーで、OpenAI APIキーではありません。`.env`はGit対象外です。

`doctor`は履歴の取得だけで接続を確認します。メッセージ投稿、設定ファイルの作成・更新、Cookieの表示は行いません。認証エラーの場合は、通常のmuse-cli側でログイン状態を更新してください。

設定の保存先が異なる場合は、`.env`の`MUSE_BRIDGE_CONFIG_DIR`を変更します。WSLの設定はWindows側から自動探索しません。既定の状態保存先は`~/.local/state/muse-airi-bridge`です。

## AIRIの設定

| 設定 | 値 |
|---|---|
| Provider | OpenAI Compatible |
| API protocol | Chat Completions |
| Base URL | `http://127.0.0.1:8000/v1/` |
| API key | `.env`に設定した`MUSE_BRIDGE_API_KEY` |
| Model | `muse-airi` |

音声認識と読み上げはAIRI側で設定します。ラッパーは文字起こし済みのテキストを扱い、音声・画像の生成APIは提供しません。

通常の会話履歴設定を使い、AIRIの補助機能（Autonomous Artist、Vision、背景レシピ、Spark通知など）にはこの接続先を使用しないでください。ラッパーは確認済みの補助呼び出しを拒否します。AIRIのfunction toolsは実行せず、`auto`/`none`のツール定義は無視します。必須のツール指定はエラーです。検索や作業の判断・実行はMuse自身が行います。

接続検証の`ping`はローカルで`pong`を返し、Museへは投稿しません。その成功はMuseへの接続成功を意味しません。実接続は`doctor`または認証付き`GET /readyz`で確認します。

## 応答と感情

Museの最初の完成返信を取得してからSSEで返します。生成途中の文字を流す方式ではありません。待機時間は既定300秒、履歴確認は1秒間隔です。

返信本文は書き換えず、AIRIへ返す直前に`<|ACT {"emotion":{"name":"neutral","intensity":1}}|>`形式のマーカーを1個付けます。明確な絵文字を保守的な辞書で判定し、不明・混在時はneutralです。引用やコード内の絵文字は判定から除外します。追加のAI呼び出しはありません。`MUSE_BRIDGE_EMOTIONS=false`で無効にできます。表情の見え方はアバターモデルが持つモーションに依存します。

## API

| エンドポイント | 説明 |
|---|---|
| `GET /healthz` | プロセスの生存確認。Museの確認はしない |
| `GET /readyz` | Museの履歴を読み取る接続診断。投稿しない |
| `GET /v1/models` | ローカルモデル一覧 |
| `POST /v1/chat/completions` | 会話。通常JSONとSSEに対応 |

`/healthz`以外は`Authorization: Bearer <local-key>`が必要です。CORSプリフライトは認証なしで応答します。CORSは全Originを許可しますが、Cookie認証は使わずBearerキーを必須にしています。既定の待受先はループバックだけです。

Chat Completionsの**AIRI向けサブセット**です。一般的なステートレスLLMの代替ではありません。通常入力にはAIRI v0.14.1の日時接頭辞が必要です。systemがあればAIRIの通常チャット用フォーマットであることを確認します。プレーンなuser本文を推測で転送するフォールバックはありません。

`messages`の文字列／テキスト配列、`stream`、`n=1`に対応します。`temperature`、`top_p`、`max_tokens`、`max_completion_tokens`は受理しますがMuseへ適用できないため無視します。使用トークン数は返しません。`stop`、JSON出力指定、ツール継続、画像・音声バイナリ・ファイル入力、Responses APIは対象外です。

接続検証は「単独の`ping`、上限16、非ストリーミング、追加指示・ツールなし」という確認済みの形式で判別します。通常会話で入力した`ping`は日時付きなのでMuseへ送信されます。レスポンスヘッダー`X-Muse-Bridge-Response`は`local-probe`と`muse`を区別します。

## 再試行・中断からの回復

同時に扱う新規発言は1件です。MuseのWeb画面や別クライアントから同時に発言しないでください。途中に別の発言が見えた場合は競合エラーにします。

Museのメインチャットに未完了の発言が見える場合も、新しい投稿を止めます。以前の処理の遅い返信を、今回の回答と誤って対応づけることを避けるためです。

リクエストのハッシュ、送信前の履歴位置、状態、取得した返信をSQLiteに保存します。同一リクエストは処理中のジョブか10分以内の完了結果を共有します。生の入力と認証情報は保存しませんが、**返信本文は状態DBに保存されます**。状態ディレクトリは会話データとして扱ってください。

接続切断はMuseの作業キャンセルになりません。投稿後は返信回収を続けます。期限切れ・送信成否不明・プロセス停止後は、不明な投稿を自動再送せず保留にします。

```powershell
# 稼働中でも保留ジョブのID・状態のみ確認できます。
uv run --env-file .env muse-airi-bridge pending

# 以下はサーバーを停止してから実行します。
# 最近の履歴と照合して返信を回収します。新規投稿はしません。
uv run --env-file .env muse-airi-bridge reconcile JOB_ID

# まだ作業中の可能性を確認し、保留を放棄する場合だけ実行します。
uv run --env-file .env muse-airi-bridge clear-pending JOB_ID --acknowledge-muse-may-still-be-working
```

照合できないジョブの保留解除はMuseの作業停止ではありません。解除後は、AIRIで新しい発言を作ってください。同じ保留リクエストの即時再送は拒否します。複数サーバーで状態DBを共有する起動も拒否します。

## 初版の制約

- 最初の完成返信より後に届く自発通知・非同期作業の続報はAIRIへ配信しません。
- AIRIの発言IDがAPIに届かないため、会話履歴を0にして同一分内に同じ本文を送るケースは区別できません。
- ユーザーが`[Context]`等の装飾用区切りと同じ文字列を入力する衝突ケースは、初版では考慮しません。
- 入力の種類の判別は確認済みのリクエスト形式に依存します。AIRIのアップデートで形式が変わった場合は、原文抽出と拒否テストを更新してから利用します。
- `reconcile`が調べる履歴は直近100イベントです。大量の更新があった後は照合できない場合があります。

## 開発とコード管理

```powershell
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
git status
```

`pyproject.toml`、`uv.lock`、Pythonバージョン指定をGitで管理します。`.venv`・認証情報・状態DB・ビルド成果物は管理対象外です。

コードを変更せずにAPI契約を検証するブラックボックステストも含みます。実機での最終確認は、AIRIから短い発言を送り、Muse履歴に原文だけが1件追加され、AIRIの表示・読み上げ・口パク・表情が動くことを確認してください。

調査根拠と内部構成は[docs/design.md](docs/design.md)、実施済みの確認と未検証部分は[docs/verification.md](docs/verification.md)を参照してください。
