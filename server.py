import os
import uuid
import base64
import certifi
import anthropic
from flask import Flask, jsonify, request, send_from_directory
from supabase import create_client
from dotenv import load_dotenv

os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

load_dotenv()

app = Flask(__name__, static_folder='.')

def get_supabase():
    return create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def get_anthropic():
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

@app.route('/')
def index():
    return send_from_directory('.', 'aura.html')

@app.route('/<path:filename>')
def static_files(filename):
    if filename.startswith('api/'):
        return "Not found", 404
    return send_from_directory('.', filename)

@app.route('/api/images')
def get_images():
    try:
        supabase = get_supabase()
        category = request.args.get('category', 'men')
        limit = int(request.args.get('limit', 20))

        bucket_map = {
            'men': 'men',
            'women': 'women',
            'accessories': 'accessories'
        }
        bucket = bucket_map.get(category, 'men')

        files = supabase.storage.from_(bucket).list()
        urls = []
        for f in (files or []):
            name = f.get('name', '')
            # Skip empty, hidden, placeholder files
            if not name or name.startswith('.') or 'Placeholder' in name or 'placeholder' in name:
                continue
            # Skip folders (id is None)
            if f.get('id') is None:
                continue
            url = supabase.storage.from_(bucket).get_public_url(name)
            urls.append(url)
            if len(urls) >= limit:
                break

        return jsonify({"urls": urls, "count": len(urls)})
    except Exception as e:
        return jsonify({"urls": [], "count": 0, "error": str(e)})

@app.route('/api/analyze', methods=['POST'])
def analyze():
    try:
        data = request.json
        image_b64 = data.get('image')
        media_type = data.get('type', 'image/jpeg')
        if not image_b64:
            return jsonify({"error": "לא התקבלה תמונה"}), 400
        supabase = get_supabase()
        file_name = f"user_{uuid.uuid4().hex[:8]}.jpg"
        supabase.storage.from_('people_images').upload(
            file_name, base64.b64decode(image_b64), {"content-type": "image/jpeg"})
        saved_url = supabase.storage.from_('people_images').get_public_url(file_name)
        client = get_anthropic()
        response = client.messages.create(
            model="claude-opus-4-5", max_tokens=200,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": "את סטייליסטית של AURA. קיבלת תמונה מהמשתמש. כתבי בדיוק: 'קיבלתי את תמונתך, במה אוכל לעזור?' — ללא שום תוספת, ללא כוכביות, בשורה אחת בלבד."}
            ]}])
        return jsonify({"analysis": response.content[0].text, "saved_url": saved_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/people")
def get_people():
    try:
        supabase = get_supabase()
        files = supabase.storage.from_("people_images").list()
        urls = []
        for f in (files or []):
            name = f.get("name", "")
            if not name or name.startswith(".") or f.get("id") is None:
                continue
            url = supabase.storage.from_("people_images").get_public_url(name)
            urls.append(url)
        return jsonify({"urls": urls, "count": len(urls)})
    except Exception as e:
        return jsonify({"urls": [], "count": 0, "error": str(e)})

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        from flask import Response, stream_with_context
        data = request.json
        client = get_anthropic()
        lang = data.get('lang', 'he')
        system_prompt = (
            "You are a fashion styling advisor for AURA luxury store.\n"
            "STRICT RULES:\n"
            "- Respond in English only\n"
            "- Every sentence MUST be on its own line using actual newline character\n"
            "- Max 7 words per line\n"
            "- Offer 2-3 concrete style options and let the customer choose\n"
            "- Never use filler words like great, wonderful, amazing\n"
            "- No asterisks, hashtags, or markdown formatting\n"
            "- End with a short question\n"
            "Example output for looking for a shirt:\n"
            "I can suggest a few options.\n"
            "Casual linen shirt?\n"
            "Or a tailored button-down?\n"
            "Which feels more like you?"
        ) if lang == 'en' else (
            "את יועצת אופנה של AURA.\n"
            "חוקים נוקשים:\n"
            "- עברית בלבד\n"
            "- כל משפט חייב להיות בשורה נפרדת עם תו שורה חדשה אמיתי\n"
            "- מקסימום 7 מילים בשורה\n"
            "- להציע 2-3 אפשרויות קונקרטיות ולתת ללקוח לבחור\n"
            "- לעולם לא להשתמש במילות מילוי כמו נהדר, מעולה, יפה\n"
            "- ללא כוכביות, סולמיות או markdown\n"
            "- לסיים בשאלה קצרה\n"
            "דוגמה לפלט על מחפשת חולצה:\n"
            "אני יכולה להציע כמה אפשרויות.\n"
            "חולצת פשתן קז'ואל?\n"
            "או כפתורים מחויטת?\n"
            "מה מרגיש יותר את?"
        )

        def generate():
            with client.messages.stream(
                model="claude-opus-4-5", max_tokens=800,
                system=system_prompt,
                messages=data.get('history', []) + [{"role": "user", "content": data.get('message', '')}]
            ) as stream:
                for text in stream.text_stream:
                    # Encode each token preserving spaces and newlines
                    import json
                    yield f"data: {json.dumps(text)}\n\n"
            yield "data: [DONE]\n\n"

        return Response(stream_with_context(generate()), mimetype='text/event-stream',
                       headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
    except Exception as e:
        return jsonify({"response": "מצטערת, אירעה שגיאה.", "error": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
