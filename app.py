from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, ImageMessage, TextSendMessage, TextMessage
import os
import requests
import base64
from openai import OpenAI

# Flaskアプリ
app = Flask(__name__)

# LINE Bot設定
line_bot_api = LineBotApi(os.getenv("CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("CHANNEL_SECRET"))

# OpenAIクライアント
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print(">>> 署名エラー")
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    print(">>> 画像メッセージ受信")

    # LINE画像取得
    try:
        headers = {"Authorization": f"Bearer {os.getenv('CHANNEL_ACCESS_TOKEN')}"}
        url = f"https://api-data.line.me/v2/bot/message/{event.message.id}/content"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
    except Exception as e:
        print(">>> 画像取得エラー：", e)
        reply_text = "画像の取得に失敗しました。もう一度送信してください。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # 画像をbase64エンコード
    try:
        image_data = response.content
        base64_image = base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        print(">>> base64エンコードエラー：", e)
        reply_text = "画像の処理に失敗しました。もう一度送信してください。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # GPT Vision APIで画像解析＋教育的指導
    try:
        prompt = """
        この画像に写っている問題を分析して、中学生から高校2年生の学習者に適した教育的な指導をしてください。

【絶対に守るべきルール】
- 答えや最終解は一切明示しない（例：x = 5、正解は...などの表現は禁止）
- 解法の考え方や途中のステップ、使うべき公式や定理の解説のみにとどめる

【具体的な方針】
- 問題の種類（代数、幾何、英文法など）を特定
- 使用する公式・定理を明記し、意味も簡単に補足
- 答えがわかっても絶対に明言しない
- 問題を解くための手順を細かく段階的に説明



【数式の表記ルール（LINE対応）】
- 分数：「分子/分母」または「分子÷分母」で表記
  例：x/2、3÷4
- 累乗：「^」または「の〇乗」で表記
  例：x^2 または xの2乗
- ルート：「√」または「ルート」で表記
  例：√2 または ルート2
- 掛け算：「×」または「・」を使用
  例：2×3、a・b
- 括弧：通常の括弧を使用 ( )
- 等号・不等号：=、<、>、≦、≧ を使用
- 三角関数：sin、cos、tan をそのまま使用
- 対数：log をそのまま使用
- 総和：Σの代わりに「〜の和」と表現
- 積分：∫の代わりに「〜の積分」と表現
- ベクトル：→を使用 例：ベクトルAB→

【表示形式】
- 見出しは【 】で囲む
- 手順は (1)、(2)、(3) で番号付け
- 箇条書きは ・ を使用
- 計算過程は矢印 → で繋ぐ
- LaTeX形式は絶対に使用しない



まず画像の内容を詳しく分析し、問題文を正確に読み取ってから指導を開始してください。
"""

        gpt_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": "auto"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1500,
            temperature=0.7
        )
        
        explanation_text = gpt_response.choices[0].message.content.strip()
        print(">>> GPT Vision返答：", explanation_text[:200] + "...")

        # テキストの長さチェックと調整
        if len(explanation_text) > 5000:
            # LINE APIの文字数制限に対応
            explanation_text = explanation_text[:4900] + "\n\n（文字数制限のため省略されました。続きが必要な場合は、もう一度画像を送信してください）"

        # テキストで返信
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=explanation_text)
        )
        print(">>> テキスト返信完了")

    except Exception as e:
        print(">>> GPT Visionエラー：", e)
        reply_text = """申し訳ありません。画像の解析に失敗しました。

以下をお試しください：
・画像をより鮮明に撮影する
・照明を明るくする
・問題全体が写るように撮影する
・手書きの場合は濃く、はっきりと書く

もう一度送信してください！"""
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    """テキストメッセージへの対応（オプション）"""
    user_message = event.message.text
    
    if user_message in ["使い方", "ヘルプ", "help"]:
        help_text = """【勉強サポートBotの使い方】

★ 数学や英語の問題の写真を送ってください！

(1) 問題を撮影
・問題全体が写るように
・明るい場所で撮影
・文字がはっきり見えるように

(2) 画像を送信
・このトークに画像を送るだけ！

(3) 解説を確認
・問題の解き方を段階的に説明
・重要なポイントを解説
・自分で解けるようにサポート

【対応科目】
・数学（中学〜高校2年レベル）
・英語（文法・読解・語彙など）
"""
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=help_text)
        )
    else:
        reply_text = "問題の画像を送信してください。数学や英語の問題を解説します！\n\n使い方を知りたい場合は「使い方」と送信してください。"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

@app.route("/", methods=['GET'])
def health_check():
    return "LINE Bot is running!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)