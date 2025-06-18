from flask import Flask, request, abort, jsonify, send_file
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, ImageMessage, TextSendMessage, TextMessage
import os
import requests
import base64
from openai import OpenAI
import logging
from datetime import datetime
import json
from collections import deque
import traceback
import hashlib
from io import BytesIO
import threading

# Flaskアプリ
app = Flask(__name__)

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# メモリ内ログストレージ（最新100件を保持）
log_storage = deque(maxlen=100)

# 画像と応答の履歴を保存（最新50件）
response_history = deque(maxlen=50)

# 画像の一時保存用辞書（メモリ内）
image_cache = {}

# カスタムログハンドラー
class MemoryLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'level': record.levelname,
            'message': self.format(record),
            'module': record.module,
            'function': record.funcName
        }
        log_storage.append(log_entry)

# メモリログハンドラーを追加
memory_handler = MemoryLogHandler()
memory_handler.setLevel(logging.INFO)
logger.addHandler(memory_handler)

# LINE Bot設定
line_bot_api = LineBotApi(os.getenv("CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("CHANNEL_SECRET"))

# OpenAIクライアント
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Slack通知設定
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

# 統計情報
stats = {
    'total_requests': 0,
    'image_requests': 0,
    'text_requests': 0,
    'errors': 0,
    'last_request': None,
    'error_types': {}
}

def send_slack_notification(title, message, color="danger", fields=None):
    """Slackに通知を送信"""
    if not SLACK_WEBHOOK_URL:
        logger.warning("Slack Webhook URLが設定されていません")
        return
    
    try:
        payload = {
            "attachments": [{
                "title": title,
                "text": message,
                "color": color,
                "fields": fields or [],
                "footer": "LINE Bot Monitor",
                "ts": int(datetime.now().timestamp())
            }]
        }
        
        response = requests.post(SLACK_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        logger.info("Slack通知送信成功")
    except Exception as e:
        logger.error(f"Slack通知送信失敗: {str(e)}")

def notify_error_async(error_type, error_message, user_id=None, additional_info=None):
    """非同期でエラー通知を送信"""
    def send_notification():
        fields = [
            {"title": "エラータイプ", "value": error_type, "short": True},
            {"title": "発生時刻", "value": datetime.now().strftime('%Y-%m-%d %H:%M:%S'), "short": True}
        ]
        
        if user_id:
            fields.append({"title": "ユーザーID", "value": user_id, "short": True})
        
        if additional_info:
            for key, value in additional_info.items():
                fields.append({"title": key, "value": str(value), "short": True})
        
        # エラータイプ別の統計を更新
        if error_type not in stats['error_types']:
            stats['error_types'][error_type] = 0
        stats['error_types'][error_type] += 1
        
        send_slack_notification(
            title=f"🚨 エラー発生: {error_type}",
            message=error_message,
            color="danger",
            fields=fields
        )
    
    # 非同期で送信
    thread = threading.Thread(target=send_notification)
    thread.daemon = True
    thread.start()

def save_image_to_cache(image_data, user_id):
    """画像をキャッシュに保存"""
    image_id = hashlib.md5(f"{user_id}_{datetime.now().isoformat()}".encode()).hexdigest()
    image_cache[image_id] = image_data
    
    # 古い画像を削除（50件を超えたら）
    if len(image_cache) > 50:
        oldest_key = next(iter(image_cache))
        del image_cache[oldest_key]
    
    return image_id

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    stats['total_requests'] += 1
    stats['last_request'] = datetime.now().isoformat()
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("署名エラー - Invalid signature")
        stats['errors'] += 1
        notify_error_async("署名エラー", "Invalid signature error", additional_info={"signature": signature[:20] + "..."})
        abort(400)
    except Exception as e:
        logger.error(f"Callback error: {str(e)}")
        logger.error(traceback.format_exc())
        stats['errors'] += 1
        notify_error_async("Callbackエラー", str(e), additional_info={"traceback": traceback.format_exc()[:500]})
        abort(500)
    
    return 'OK'

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    stats['image_requests'] += 1
    user_id = event.source.user_id
    
    logger.info(f"画像メッセージ受信 - User: {user_id}")
    
    # 履歴エントリを作成
    history_entry = {
        'id': hashlib.md5(f"{user_id}_{datetime.now().isoformat()}".encode()).hexdigest(),
        'timestamp': datetime.now().isoformat(),
        'user_id': user_id,
        'type': 'image',
        'image_id': None,
        'response': None,
        'error': None,
        'processing_time': None
    }
    
    start_time = datetime.now()

    # LINE画像取得
    try:
        headers = {"Authorization": f"Bearer {os.getenv('CHANNEL_ACCESS_TOKEN')}"}
        url = f"https://api-data.line.me/v2/bot/message/{event.message.id}/content"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        image_data = response.content
        logger.info(f"画像取得成功 - Size: {len(image_data)} bytes")
        
        # 画像をキャッシュに保存
        image_id = save_image_to_cache(image_data, user_id)
        history_entry['image_id'] = image_id
        
    except Exception as e:
        error_msg = f"画像取得エラー - User: {user_id}, Error: {str(e)}"
        logger.error(error_msg)
        stats['errors'] += 1
        history_entry['error'] = str(e)
        response_history.append(history_entry)
        
        notify_error_async("画像取得エラー", str(e), user_id=user_id)
        
        reply_text = "画像の取得に失敗しました。もう一度送信してください。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # 画像をbase64エンコード
    try:
        base64_image = base64.b64encode(image_data).decode('utf-8')
        logger.info(f"Base64エンコード成功 - User: {user_id}")
    except Exception as e:
        error_msg = f"Base64エンコードエラー - User: {user_id}, Error: {str(e)}"
        logger.error(error_msg)
        stats['errors'] += 1
        history_entry['error'] = str(e)
        response_history.append(history_entry)
        
        notify_error_async("Base64エンコードエラー", str(e), user_id=user_id)
        
        reply_text = "画像の処理に失敗しました。もう一度送信してください。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return

    # GPT Vision APIで画像解析＋教育的指導
    try:
        prompt = """
        この画像に写っている問題を分析して、中学生から高校2年生の学習者に適した教育的な指導をしてください。

【絶対に守ること】
- 計算しなくていいから、解き方の手順だけ教えてください
- 数式と物理の表記はLINEで使える形式にしてください
- 日本の中学生や高校生の知識の範囲内で説明してください
- LaTeX形式は絶対に使用せず、LINEで使える形式で説明してください

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
- 考え方と手順のみ表示
- 手順は (1)、(2)、(3) で番号付け
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
        processing_time = (datetime.now() - start_time).total_seconds()
        
        logger.info(f"GPT Vision処理成功 - User: {user_id}, Processing time: {processing_time:.2f}s, Response length: {len(explanation_text)}")

        # テキストの長さチェックと調整
        if len(explanation_text) > 5000:
            explanation_text = explanation_text[:4900] + "\n\n（文字数制限のため省略されました。続きが必要な場合は、もう一度画像を送信してください）"
            logger.warning(f"Response truncated - User: {user_id}, Original length: {len(explanation_text)}")

        # 履歴に応答を保存
        history_entry['response'] = explanation_text
        history_entry['processing_time'] = processing_time
        response_history.append(history_entry)

        # テキストで返信
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=explanation_text)
        )
        logger.info(f"返信完了 - User: {user_id}")

    except Exception as e:
        error_msg = f"GPT Visionエラー - User: {user_id}, Error: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        stats['errors'] += 1
        
        history_entry['error'] = str(e)
        history_entry['processing_time'] = (datetime.now() - start_time).total_seconds()
        response_history.append(history_entry)
        
        # エラーの詳細情報を収集
        error_info = {
            "image_size": len(image_data),
            "processing_time": f"{history_entry['processing_time']:.2f}s"
        }
        
        notify_error_async("GPT Visionエラー", str(e), user_id=user_id, additional_info=error_info)
        
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
    stats['text_requests'] += 1
    user_id = event.source.user_id
    user_message = event.message.text
    
    logger.info(f"テキストメッセージ受信 - User: {user_id}, Message: {user_message}")
    
    # 履歴エントリを作成
    history_entry = {
        'id': hashlib.md5(f"{user_id}_{datetime.now().isoformat()}".encode()).hexdigest(),
        'timestamp': datetime.now().isoformat(),
        'user_id': user_id,
        'type': 'text',
        'message': user_message,
        'response': None
    }
    
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
        
        history_entry['response'] = help_text
        response_history.append(history_entry)
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=help_text)
        )
        logger.info(f"ヘルプメッセージ送信 - User: {user_id}")
    else:
        reply_text = "問題の画像を送信してください。数学や英語の問題を解説します！\n\n使い方を知りたい場合は「使い方」と送信してください。"
        history_entry['response'] = reply_text
        response_history.append(history_entry)
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

@app.route("/", methods=['GET'])
def health_check():
    return "LINE Bot is running!"

# ログ閲覧エンドポイント
@app.route("/logs", methods=['GET'])
def view_logs():
    auth_token = request.args.get('token')
    expected_token = os.getenv('LOG_VIEW_TOKEN', 'your-secret-token')
    
    if auth_token != expected_token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    level = request.args.get('level', 'all').upper()
    limit = int(request.args.get('limit', 50))
    
    filtered_logs = list(log_storage)
    if level != 'ALL':
        filtered_logs = [log for log in filtered_logs if log['level'] == level]
    
    filtered_logs = filtered_logs[-limit:]
    
    return jsonify({
        'logs': filtered_logs,
        'stats': stats,
        'total_logs': len(log_storage),
        'filtered_count': len(filtered_logs)
    })

# 統計情報エンドポイント
@app.route("/stats", methods=['GET'])
def view_stats():
    auth_token = request.args.get('token')
    expected_token = os.getenv('LOG_VIEW_TOKEN', 'your-secret-token')
    
    if auth_token != expected_token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    return jsonify({
        'stats': stats,
        'uptime': 'Check Render dashboard',
        'current_time': datetime.now().isoformat(),
        'cache_size': {
            'images': len(image_cache),
            'history': len(response_history)
        }
    })

# 履歴閲覧エンドポイント
@app.route("/history", methods=['GET'])
def view_history():
    auth_token = request.args.get('token')
    expected_token = os.getenv('LOG_VIEW_TOKEN', 'your-secret-token')
    
    if auth_token != expected_token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    limit = int(request.args.get('limit', 20))
    user_id = request.args.get('user_id')
    
    history = list(response_history)
    
    # ユーザーIDでフィルタリング
    if user_id:
        history = [h for h in history if h.get('user_id') == user_id]
    
    # 最新のものから取得
    history = history[-limit:]
    history.reverse()
    
    return jsonify({
        'history': history,
        'total_count': len(response_history),
        'filtered_count': len(history)
    })

# 画像取得エンドポイント
@app.route("/image/<image_id>", methods=['GET'])
def get_image(image_id):
    auth_token = request.args.get('token')
    expected_token = os.getenv('LOG_VIEW_TOKEN', 'your-secret-token')
    
    if auth_token != expected_token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    if image_id not in image_cache:
        return jsonify({'error': 'Image not found'}), 404
    
    image_data = image_cache[image_id]
    return send_file(
        BytesIO(image_data),
        mimetype='image/jpeg',
        as_attachment=False,
        download_name=f'{image_id}.jpg'
    )

# 精度確認用のダッシュボード
@app.route("/dashboard", methods=['GET'])
def dashboard():
    auth_token = request.args.get('token')
    expected_token = os.getenv('LOG_VIEW_TOKEN', 'your-secret-token')
    
    if auth_token != expected_token:
        return "Unauthorized", 401
    
    # シンプルなHTMLダッシュボード
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>LINE Bot Monitor Dashboard</title>
        <meta charset="utf-8">
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
            .container { max-width: 1200px; margin: 0 auto; }
            .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
            .stat-box { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .stat-value { font-size: 2em; font-weight: bold; color: #333; }
            .stat-label { color: #666; margin-top: 5px; }
            .history { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .history-item { border-bottom: 1px solid #eee; padding: 15px 0; }
            .history-item:last-child { border-bottom: none; }
            .error { color: #d32f2f; }
            .success { color: #388e3c; }
            .image-preview { max-width: 200px; max-height: 200px; margin: 10px 0; }
            .response-text { background: #f5f5f5; padding: 10px; border-radius: 4px; margin-top: 10px; white-space: pre-wrap; }
            h1, h2 { color: #333; }
            .refresh-btn { background: #1976d2; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; }
            .refresh-btn:hover { background: #1565c0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>LINE Bot Monitor Dashboard</h1>
            <button class="refresh-btn" onclick="location.reload()">更新</button>
            
            <div class="stats" id="stats"></div>
            <div class="history">
                <h2>最近の処理履歴</h2>
                <div id="history"></div>
            </div>
        </div>
        
        <script>
            const token = new URLSearchParams(window.location.search).get('token');
            
            // 統計情報を取得
            fetch(`/stats?token=${token}`)
                .then(res => res.json())
                .then(data => {
                    const statsDiv = document.getElementById('stats');
                    statsDiv.innerHTML = `
                        <div class="stat-box">
                            <div class="stat-value">${data.stats.total_requests}</div>
                            <div class="stat-label">総リクエスト数</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-value">${data.stats.image_requests}</div>
                            <div class="stat-label">画像リクエスト</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-value">${data.stats.errors}</div>
                            <div class="stat-label">エラー数</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-value">${data.cache_size.images}</div>
                            <div class="stat-label">キャッシュ画像数</div>
                        </div>
                    `;
                });
            
            // 履歴を取得
            fetch(`/history?token=${token}&limit=10`)
                .then(res => res.json())
                .then(data => {
                    const historyDiv = document.getElementById('history');
                    historyDiv.innerHTML = data.history.map(item => `
                        <div class="history-item">
                            <div><strong>時刻:</strong> ${new Date(item.timestamp).toLocaleString('ja-JP')}</div>
                            <div><strong>ユーザーID:</strong> ${item.user_id}</div>
                            <div><strong>タイプ:</strong> ${item.type}</div>
                            ${item.processing_time ? `<div><strong>処理時間:</strong> ${item.processing_time.toFixed(2)}秒</div>` : ''}
                            ${item.error ? `<div class="error"><strong>エラー:</strong> ${item.error}</div>` : '<div class="success">成功</div>'}
                            ${item.image_id ? `
                                <div>
                                    <img src="/image/${item.image_id}?token=${token}" class="image-preview" alt="送信画像">
                                </div>
                            ` : ''}
                            ${item.response ? `
                                <div class="response-text">
                                    <strong>応答:</strong><br>
                                    ${item.response.substring(0, 500)}${item.response.length > 500 ? '...' : ''}
                                </div>
                            ` : ''}
                        </div>
                    `).join('');
                });
        </script>
    </body>
    </html>
    """
    
    return html

if __name__ == "__main__":
    logger.info("LINE Bot starting...")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)