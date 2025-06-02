from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, ImageMessage, TextSendMessage
import os
import requests
from io import BytesIO
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
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        except Exception as reply_error:
            print(">>> LINE返信エラー（画像取得失敗時）：", reply_error)
        return

    # 画像をbase64エンコード
    try:
        image_data = response.content
        base64_image = base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        print(">>> base64エンコードエラー：", e)
        reply_text = "画像の処理に失敗しました。もう一度送信してください。"
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        except Exception as reply_error:
            print(">>> LINE返信エラー（エンコード失敗時）：", reply_error)
        return

    # GPT Vision APIで画像解析＋教育的指導
    try:
        prompt = """
この画像に写っている問題を分析して、中学生から高校2年生の学習者に適した教育的な指導をしてください。

【指導方針】
- 数学問題の場合：
  * 問題の種類を特定（代数、幾何、関数、確率統計など）
  * 解法の手順を段階的に説明
  * 使用する公式や定理を明記
  * 計算過程で注意すべきポイントを指摘
  * 類似問題への応用方法を提示

- 英語問題の場合：
  * 問題の種類を特定（文法、読解、語彙、作文など）
  * 解答のアプローチを説明
  * 重要な文法ポイントや語彙を解説
  * 英文の構造分析（長文読解の場合）
  * 覚えておくべきポイントを整理

【注意事項】
- 直接的な答えは示さない
- 学習者が自分で考えて解けるよう導く
- 中学〜高校2年レベルの知識範囲で説明
- 分かりやすく、励ましの言葉も含める
- 問題が読み取れない場合は、より鮮明な画像を求める

まず画像の内容を詳しく分析し、問題文を正確に読み取ってから指導を開始してください。
"""

        gpt_response = client.chat.completions.create(
            model="gpt-4o",  # GPT-4 Visionに最適化
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
                                "detail": "high"  # 高解像度で解析
                            }
                        }
                    ]
                }
            ],
            max_tokens=1500,
            temperature=0.7
        )
        
        reply_text = gpt_response.choices[0].message.content.strip()
        print(">>> GPT Vision返答：", reply_text)

    except Exception as e:
        print(">>> GPT Visionエラー：", e)
        reply_text = """
申し訳ありません。画像の解析に失敗しました。

以下をお試しください：
📸 画像をより鮮明に撮影する
💡 照明を明るくする
📐 問題全体が写るように撮影する
✏️ 手書きの場合は濃く、はっきりと書く

もう一度送信してください！
"""

    # 返信文が長すぎる場合は分割（LINEの文字数制限対応）
    if len(reply_text) > 5000:
        reply_text = reply_text[:4900] + "\n\n（続きが必要でしたら、もう一度画像を送信してください）"

    # LINE返信
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        print(">>> LINE返信完了")
    except Exception as e:
        print(">>> LINE返信エラー：", e)

@app.route("/", methods=['GET'])
def health_check():
    return "LINE Bot is running!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)