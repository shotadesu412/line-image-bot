from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, ImageMessage, TextSendMessage, ImageSendMessage
import os
import requests
from io import BytesIO
import base64
from openai import OpenAI
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib import rcParams
import japanize_matplotlib  # 日本語フォント対応
import textwrap

# Flaskアプリ
app = Flask(__name__)

# LINE Bot設定
line_bot_api = LineBotApi(os.getenv("CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("CHANNEL_SECRET"))

# OpenAIクライアント
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# matplotlib設定
rcParams['font.size'] = 12
rcParams['axes.unicode_minus'] = False

def create_explanation_image(text, filename="explanation.png"):
    """解説テキストを画像に変換（シンプルな白黒デザイン）"""
    # フォント設定
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.size'] = 12
    
    # 画像サイズを最適化（縦は内容に応じて動的に調整）
    fig, ax = plt.subplots(figsize=(8, 10), facecolor='white')
    ax.axis('off')
    
    # テキストを処理
    lines = text.split('\n')
    y_position = 0.98
    line_height = 0.025
    
    for line in lines:
        if line.strip() == '':
            y_position -= line_height * 0.5
            continue
            
        # 見出しの判定
        if line.startswith('【') and line.endswith('】'):
            # 見出しは太字で少し大きく
            ax.text(0.05, y_position, line, 
                   fontsize=14, fontweight='bold', 
                   transform=ax.transAxes, 
                   verticalalignment='top',
                   color='black')
            y_position -= line_height * 1.5
            
        elif line.startswith('📝') or line.startswith('💡') or line.startswith('⚠️') or line.startswith('✅'):
            # 重要ポイント（絵文字付き）
            wrapped_lines = textwrap.wrap(line, width=50)
            for wrapped_line in wrapped_lines:
                ax.text(0.05, y_position, wrapped_line, 
                       fontsize=12, 
                       transform=ax.transAxes, 
                       verticalalignment='top',
                       color='black')
                y_position -= line_height
            y_position -= line_height * 0.3
            
        elif line.startswith('*') or line.startswith('•') or line.startswith('・'):
            # 箇条書き
            clean_line = line.lstrip('*•・ ')
            wrapped_lines = textwrap.wrap('  • ' + clean_line, width=48)
            for wrapped_line in wrapped_lines:
                ax.text(0.05, y_position, wrapped_line, 
                       fontsize=11, 
                       transform=ax.transAxes, 
                       verticalalignment='top',
                       color='black')
                y_position -= line_height
                
        elif '＝' in line or '=' in line or '→' in line:
            # 数式や計算式（インデントして目立たせる）
            ax.text(0.1, y_position, line.strip(), 
                   fontsize=12, 
                   transform=ax.transAxes, 
                   verticalalignment='top',
                   color='black',
                   fontfamily='monospace')  # 等幅フォント
            y_position -= line_height * 1.3
            
        else:
            # 通常のテキスト
            wrapped_lines = textwrap.wrap(line, width=52)
            for wrapped_line in wrapped_lines:
                ax.text(0.05, y_position, wrapped_line, 
                       fontsize=11, 
                       transform=ax.transAxes, 
                       verticalalignment='top',
                       color='black')
                y_position -= line_height
        
        # ページの下端に達した場合の処理
        if y_position < 0.05:
            break
    
    # シンプルな黒枠
    rect = patches.Rectangle((0.02, 0.02), 0.96, 0.96, 
                           linewidth=1, edgecolor='black', 
                           facecolor='none', transform=ax.transAxes)
    ax.add_patch(rect)
    
    # 画像を保存（品質とサイズのバランスを最適化）
    plt.tight_layout()
    buffer = BytesIO()
    plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight', 
                facecolor='white', edgecolor='none')
    buffer.seek(0)
    plt.close()
    
    return buffer

def upload_image_to_imgur(image_buffer):
    """画像をImgurにアップロードしてURLを取得"""
    imgur_client_id = os.getenv("IMGUR_CLIENT_ID")
    if not imgur_client_id:
        print(">>> IMGUR_CLIENT_IDが設定されていません")
        return None
    
    headers = {'Authorization': f'Client-ID {imgur_client_id}'}
    
    image_buffer.seek(0)
    image_data = base64.b64encode(image_buffer.read()).decode()
    
    response = requests.post(
        'https://api.imgur.com/3/image',
        headers=headers,
        data={'image': image_data, 'type': 'base64'}
    )
    
    if response.status_code == 200:
        data = response.json()
        return data['data']['link']
    else:
        print(f">>> Imgurアップロードエラー: {response.status_code}")
        return None

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

【表記ルール】
- 数式は分かりやすく日本語も交えて説明する
- 分数は「分子/分母」の形で表記（例：3/4）
- 累乗は「^」を使用（例：x^2はxの2乗）
- ルートは「√」を使用（例：√2）
- 複雑な式は段階的に分解して説明
- 専門用語は必要最小限にして、使う場合は説明を加える
- 重要なポイントには絵文字を使用（💡ヒント、📝ポイント、⚠️注意、✅確認）

【注意事項】
- 直接的な答えは示さない
- 学習者が自分で考えて解けるよう導く
- 中学〜高校2年レベルの知識範囲で説明
- 分かりやすく、励ましの言葉も含める
- 問題が読み取れない場合は、より鮮明な画像を求める

まず画像の内容を詳しく分析し、問題文を正確に読み取ってから指導を開始してください。
"""

        gpt_response = client.chat.completions.create(
            model="gpt-4o-mini",  # コスト最適化のためminiモデルを使用
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
                                "detail": "low"  # コスト削減のため低解像度モードを使用
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000,  # トークン数を適切に制限
            temperature=0.7
        )
        
        explanation_text = gpt_response.choices[0].message.content.strip()
        print(">>> GPT Vision返答：", explanation_text[:200] + "...")

        # 解説画像を生成
        image_buffer = create_explanation_image(explanation_text)
        
        # 画像をアップロード
        image_url = upload_image_to_imgur(image_buffer)
        
        if image_url:
            # 画像として返信
            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(
                    original_content_url=image_url,
                    preview_image_url=image_url
                )
            )
            print(">>> 画像返信完了")
        else:
            # アップロード失敗時はテキストで返信
            if len(explanation_text) > 5000:
                text_reply = explanation_text[:4900] + "\n\n（続きが必要でしたら、もう一度画像を送信してください）"
            else:
                text_reply = explanation_text
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=text_reply)
            )
            print(">>> テキスト返信完了（画像アップロード失敗）")

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
        try:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
        except Exception as reply_error:
            print(">>> LINE返信エラー：", reply_error)

@app.route("/", methods=['GET'])
def health_check():
    return "LINE Bot is running!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)