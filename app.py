from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, ImageMessage, TextSendMessage
import os
import requests
from io import BytesIO
import base64
from openai import OpenAI
import re

# Flaskアプリ
app = Flask(__name__)

# LINE Bot設定
line_bot_api = LineBotApi(os.getenv("CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("CHANNEL_SECRET"))

# OpenAIクライアント
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# LaTeX変換辞書（中高数学でよく使う記法）
LATEX_CONVERSIONS = {
    # 基本的な数学記号
    r'\times': '×',
    r'\div': '÷',
    r'\pm': '±',
    r'\mp': '∓',
    r'\neq': '≠',
    r'\leq': '≦',
    r'\geq': '≧',
    r'\ll': '≪',
    r'\gg': '≫',
    r'\approx': '≈',
    r'\equiv': '≡',
    r'\sim': '∼',
    r'\propto': '∝',
    r'\infty': '∞',
    r'\partial': '∂',
    r'\nabla': '∇',
    
    # 集合記号
    r'\in': '∈',
    r'\notin': '∉',
    r'\subset': '⊂',
    r'\supset': '⊃',
    r'\subseteq': '⊆',
    r'\supseteq': '⊇',
    r'\cup': '∪',
    r'\cap': '∩',
    r'\emptyset': '∅',
    
    # 論理記号
    r'\forall': '∀',
    r'\exists': '∃',
    r'\neg': '¬',
    r'\land': '∧',
    r'\lor': '∨',
    r'\Rightarrow': '⇒',
    r'\Leftrightarrow': '⇔',
    r'\therefore': '∴',
    r'\because': '∵',
    
    # ギリシャ文字（よく使うもの）
    r'\alpha': 'α',
    r'\beta': 'β',
    r'\gamma': 'γ',
    r'\delta': 'δ',
    r'\epsilon': 'ε',
    r'\theta': 'θ',
    r'\lambda': 'λ',
    r'\mu': 'μ',
    r'\pi': 'π',
    r'\sigma': 'σ',
    r'\tau': 'τ',
    r'\phi': 'φ',
    r'\omega': 'ω',
    r'\Delta': 'Δ',
    r'\Sigma': 'Σ',
    r'\Pi': 'Π',
    r'\Omega': 'Ω',
    
    # 矢印
    r'\rightarrow': '→',
    r'\leftarrow': '←',
    r'\leftrightarrow': '↔',
    r'\uparrow': '↑',
    r'\downarrow': '↓',
    
    # その他
    r'\cdot': '・',
    r'\ldots': '…',
    r'\cdots': '⋯',
    r'\angle': '∠',
    r'\perp': '⊥',
    r'\parallel': '∥',
    r'\triangle': '△',
    r'\square': '□',
    r'\circ': '○',
    r'\bullet': '•',
    r'\star': '★',
}

def convert_latex_to_readable(text):
    """LaTeX記法を読みやすい日本語表記に変換"""
    # 基本的な記号の置換
    for latex, unicode_char in LATEX_CONVERSIONS.items():
        text = text.replace(latex, unicode_char)
    
    # 分数の変換: \frac{a}{b} → (a)/(b)
    text = re.sub(r'\\frac\{([^}]+)\}\{([^}]+)\}', r'(\1)/(\2)', text)
    
    # 平方根の変換: \sqrt{x} → √(x)
    text = re.sub(r'\\sqrt\{([^}]+)\}', r'√(\1)', text)
    
    # 累乗の変換: x^{n} → x^n, x^2 → x²
    text = re.sub(r'\^{([^}]+)}', r'^\1', text)
    text = text.replace('^2', '²').replace('^3', '³')
    
    # 下付き文字の変換: x_{n} → x_n
    text = re.sub(r'_{([^}]+)}', r'_\1', text)
    
    # 三角関数・対数関数などの変換
    functions = ['sin', 'cos', 'tan', 'log', 'ln', 'exp', 'lim', 'max', 'min']
    for func in functions:
        text = text.replace(f'\\{func}', func)
    
    # 行列の簡略化: \begin{pmatrix}...\end{pmatrix} → [...]
    text = re.sub(r'\\begin\{pmatrix\}(.*?)\\end\{pmatrix\}', r'[\1]', text, flags=re.DOTALL)
    text = re.sub(r'\\begin\{bmatrix\}(.*?)\\end\{bmatrix\}', r'[\1]', text, flags=re.DOTALL)
    
    # ベクトルの変換: \vec{a} → a→
    text = re.sub(r'\\vec\{([^}]+)\}', r'\1→', text)
    
    # 積分記号の簡略化: \int → ∫
    text = text.replace(r'\int', '∫')
    
    # 総和記号の簡略化: \sum → Σ
    text = text.replace(r'\sum', 'Σ')
    
    # 数式環境の削除
    text = re.sub(r'\$\$([^$]+)\$\$', r'\1', text)
    text = re.sub(r'\$([^$]+)\$', r'\1', text)
    text = re.sub(r'\\begin\{equation\}(.*?)\\end\{equation\}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\\begin\{align\}(.*?)\\end\{align\}', r'\1', text, flags=re.DOTALL)
    
    # 改行コマンドの変換
    text = text.replace(r'\\\\', '\n')
    text = text.replace(r'\n\n\n', '\n\n')  # 連続改行の調整
    
    # 不要なバックスラッシュの削除
    text = re.sub(r'\\([a-zA-Z]+)', r'\1', text)
    
    # スペースの調整
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    
    return text

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
- 分かりやすく
- 問題が読み取れない場合は、より鮮明な画像を求める

【数式の表記について】
- 数式はLaTeX記法を使用して正確に表現してください
- 例: 分数は\frac{a}{b}、平方根は\sqrt{x}、累乗はx^{n}のように記述

まず画像の内容を詳しく分析し、問題文を正確に読み取ってから指導を開始してください。
"""

        # temperatureを少し下げて、より一貫性のある回答を生成
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
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1500,
            temperature=0.5  # より一貫性のある回答のため温度を下げる
        )
        
        reply_text = gpt_response.choices[0].message.content.strip()
        print(">>> GPT Vision返答（変換前）：", reply_text[:200] + "...")
        
        # LaTeX記法を読みやすい形式に変換
        reply_text = convert_latex_to_readable(reply_text)
        print(">>> LaTeX変換後：", reply_text[:200] + "...")

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