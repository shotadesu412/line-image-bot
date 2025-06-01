from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, ImageMessage, TextSendMessage
import os
import json
import requests
from io import BytesIO
from PIL import Image, ImageEnhance
from google.cloud import vision
from google.oauth2 import service_account
import openai

# Flaskアプリ
app = Flask(__name__)

# LINE Bot設定
line_bot_api = LineBotApi(os.getenv("CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("CHANNEL_SECRET"))

# OpenAIクライアント（openai>=1.0.0）
client = openai.OpenAI()

# Google Cloud Vision API 認証（Renderの環境変数から）
credentials_info = json.loads(os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"])
credentials = service_account.Credentials.from_service_account_info(credentials_info)
vision_client = vision.ImageAnnotatorClient(credentials=credentials)

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
    except Exception as e:
        print(">>> 画像取得エラー：", e)
        return

    # OCR処理（コントラスト強調あり）
    try:
        pil_image = Image.open(BytesIO(response.content)).convert("L")
        enhancer = ImageEnhance.Contrast(pil_image)
        enhanced_image = enhancer.enhance(2.0)

        img_byte_arr = BytesIO()
        enhanced_image.save(img_byte_arr, format='PNG')
        image = vision.Image(content=img_byte_arr.getvalue())

        response = vision_client.text_detection(image=image)
        texts = response.text_annotations
        extracted_text = texts[0].description.strip() if texts else ""
        print(">>> OCR抽出結果：", extracted_text)

    except Exception as e:
        print(">>> OCRエラー：", e)
        extracted_text = ""

    if not extracted_text or len(extracted_text) < 10:
        reply_text = "画像の文字がうまく読み取れませんでした。もう少し明るく、ピントの合った写真を送ってください。"
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        except Exception as e:
            print(">>> LINE返信エラー（OCR失敗時）：", e)
        return

    # GPTプロンプト
    try:
        prompt = f"""
以下は中学生向けの学習用問題文です。
あなたは教師として、この問題にどう取り組めば良いか、生徒が自力で解けるように丁寧に導いてあげてください。

- 答えは明言しないでください
- 問題の内容を理解し、どういう手順で考えれば良いかを説明してください
- 使うべき公式・考え方、注目ポイントなどを明確に伝えてください
- 中学生が学ぶ範囲（教科書レベル）で解説してください

--- 問題文ここから ---
{extracted_text}
--- 問題文ここまで ---
"""
        gpt_response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )
        reply_text = gpt_response.choices[0].message.content.strip()
        print(">>> GPT返答：", reply_text)

    except Exception as e:
        print(">>> GPTエラー：", e)
        reply_text = "考え方の説明に失敗しました。もう一度試してください。"

    # LINE返信
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
    except Exception as e:
        print(">>> LINE返信エラー：", e)

if __name__ == "__main__":
    app.run()
