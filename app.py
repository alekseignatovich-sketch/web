import os
import requests
import base64
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from github import Github

# Загрузка переменных окружения
load_dotenv()

app = Flask(__name__)

# Конфигурация
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GITHUB_USER = os.getenv("GITHUB_USER")

if not all([GITHUB_TOKEN, GROQ_API_KEY, GITHUB_USER]):
    raise Exception("❌ Ошибка: Проверьте файл .env. Все ключи должны быть заполнены.")

def generate_code(prompt):
    """Генерация HTML кода через Groq API"""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_prompt = """
    Ты эксперт-веб разработчик. Твоя задача - написать полный код для одностраничного сайта (HTML + CSS внутри <style>).
    
    ТРЕБОВАНИЯ:
    1. Сайт должен быть современным, адаптивным и красивым.
    2. Весь CSS должен быть внутри тега <style> в head.
    3. Используй современные шрифты (Google Fonts).
    
    РАБОТА С ИЗОБРАЖЕНИЯМИ:
    1. Для всех изображений используй сервис Pollinations AI.
    2. Формат ссылок: https://image.pollinations.ai/prompt/{описание_на_английском}
    3. Пример: <img src="https://image.pollinations.ai/prompt/modern_coffee_shop" alt="Coffee">
    4. Описания в URL должны быть на английском, слова через нижнее подчеркивание.
    5. Подбирай описания релевантными контенту.
    
    ОТВЕТ:
    - Верни ТОЛЬКО чистый HTML код.
    - Никаких markdown блоков (```html), никаких пояснений.
    """

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Создай сайт: {prompt}"}
        ],
        "model": "llama3-70b-8192",
        "temperature": 0.7,
        "max_tokens": 4096
    }

    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content']

def upload_to_github(code_content, site_name):
    """Создание репозитория и загрузка файлов на GitHub"""
    g = Github(GITHUB_TOKEN)
    user = g.get_user()
    
    # Очистка имени для URL
    safe_name = "".join(c if c.isalnum() else "-" for c in site_name).lower()
    repo_name = f"site-{safe_name}"
    
    # Создание репозитория
    try:
        repo = user.create_repo(repo_name, private=False, auto_init=False)
    except Exception as e:
        # Если репозиторий уже существует, добавляем случайное число
        import random
        repo_name = f"site-{safe_name}-{random.randint(1000, 9999)}"
        repo = user.create_repo(repo_name, private=False, auto_init=False)
    
    # Кодирование контента в base64
    content_bytes = code_content.encode('utf-8')
    content_base64 = base64.b64encode(content_bytes).decode('utf-8')
    
    # Загрузка index.html
    repo.create_file(
        path="index.html",
        message="Initial commit: AI generated site with Pollinations images",
        content=content_base64
    )
    
    return repo.html_url, repo.name

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    prompt = data.get('prompt')
    site_name = data.get('name', 'my-site')
    
    if not prompt:
        return jsonify({"error": "Введите описание сайта"}), 400

    try:
        print(f"🤖 Генерация кода для: {prompt}")
        html_code = generate_code(prompt)
        
        # Очистка от возможных markdown остатков
        html_code = html_code.replace("```html", "").replace("```", "").strip()
        
        print("📤 Загрузка на GitHub...")
        repo_url, repo_name = upload_to_github(html_code, site_name)
        
        # Ссылка на GitHub Pages
        pages_url = f"https://{GITHUB_USER}.github.io/{repo_name}/"

        return jsonify({
            "success": True, 
            "repo": repo_url, 
            "preview": pages_url,
            "code": html_code
        })

    except Exception as e:
        print(f"❌ Ошибка: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
