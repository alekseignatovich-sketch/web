import os
import re
import requests
import base64
import random
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from github import Github, GithubException

# Загрузка переменных окружения из .env (локально)
load_dotenv()

app = Flask(__name__)

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GITHUB_USER = os.getenv("GITHUB_USER")

# Проверка наличия всех ключей
if not all([GITHUB_TOKEN, GROQ_API_KEY, GITHUB_USER]):
    raise RuntimeError(
        "❌ Ошибка конфигурации! Проверьте переменные окружения:\n"
        "- GITHUB_TOKEN\n"
        "- GROQ_API_KEY\n"
        "- GITHUB_USER"
    )

# =============================================================================
# ФУНКЦИИ
# =============================================================================

def generate_code(prompt: str) -> str:
    """
    Генерация HTML-кода сайта через Groq API (модель Llama 3)
    """
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_prompt = """
    Ты эксперт-веб разработчик уровня Senior. Твоя задача — написать полный, готовый к использованию код для одностраничного сайта (HTML + CSS внутри тега <style>).

    🔧 ТЕХНИЧЕСКИЕ ТРЕБОВАНИЯ:
    1. Весь CSS должен быть внутри <style> в <head> (никаких внешних файлов).
    2. Используй современные практики: flexbox/grid, CSS-переменные, адаптивность.
    3. Подключи Google Fonts (Inter или Roboto) для красивого шрифта.
    4. Добавь плавные анимации и hover-эффекты для интерактивности.
    5. Код должен быть валидным HTML5.

    🎨 РАБОТА С ИЗОБРАЖЕНИЯМИ (Pollinations AI):
    1. Для ВСЕХ изображений используй сервис: https://image.pollinations.ai/prompt/{описание}
    2. Формат ссылки: https://image.pollinations.ai/prompt/{описание_на_английском}
    3. Правила описания:
       - Только английский язык
       - Слова через нижнее подчеркивание: modern_office_workspace
       - Без пробелов и спецсимволов
       - Будь конкретен: не "image", а "happy_businesswoman_presentation"
    4. Примеры:
       <img src="https://image.pollinations.ai/prompt/cozy_coffee_shop_interior" alt="Coffee Shop">
       <div style="background-image: url('https://image.pollinations.ai/prompt/sunset_mountain_landscape')">
    5. Для аватарок добавь параметр width: ?width=200

    📦 ФОРМАТ ОТВЕТА:
    - Верни ТОЛЬКО чистый HTML-код
    - БЕЗ markdown-блоков (```html), БЕЗ пояснений, БЕЗ комментариев о коде
    - Начни сразу с <!DOCTYPE html>
    - Если генерируешь JavaScript, помести его в <script> в конце <body>

    🎯 ЦЕЛЬ:
    Создать красивый, профессиональный сайт, который сразу работает после открытия в браузере.
    """

    payload = {
        "messages": [
            {"role": "system", " "content": system_prompt},
            {"role": "user", "content": f"Создай сайт: {prompt}"}
        ],
        "model": "llama3-70b-8192",  # Мощная и быстрая модель
        "temperature": 0.7,            # Баланс креативности и точности
        "max_tokens": 8192,            # Максимальный контекст для больших сайтов
        "top_p": 0.95
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        result = response.json()
        return result['choices'][0]['message']['content']
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Ошибка Groq API: {str(e)}")


def clean_html_code(code: str) -> str:
    """
    Очистка кода от markdown-разметки и лишних символов
    """
    # Убираем markdown блоки
    code = re.sub(r'^```html\s*', '', code, flags=re.MULTILINE)
    code = re.sub(r'\s*```$', '', code, flags=re.MULTILINE)
    code = re.sub(r'^```\s*', '', code, flags=re.MULTILINE)
    
    # Убираем возможные комментарии ИИ в начале/конце
    code = code.strip()
    
    # Если код не начинается с <!DOCTYPE или <html, пробуем найти первый <
    if not code.startswith('<'):
        match = re.search(r'(<!DOCTYPE[^>]*>|<html[^>]*>|<head[^>]*>)', code, re.IGNORECASE)
        if match:
            code = code[match.start():]
    
    return code


def upload_to_github(code_content: str, site_name: str) -> tuple[str, str]:
    """
    Создание репозитория и загрузка index.html на GitHub
    
    Returns:
        tuple: (url репозитория, имя репозитория)
    """
    g = Github(GITHUB_TOKEN)
    user = g.get_user()
    
    # Санитизация имени для GitHub (только латиница, цифры, дефисы)
    safe_name = re.sub(r'[^a-zA-Z0-9-]', '-', site_name.lower())
    safe_name = re.sub(r'-+', '-', safe_name).strip('-')
    
    # Формируем уникальное имя репозитория
    base_repo_name = f"site-{safe_name}"
    
    # Проверяем существование и добавляем суффикс при коллизии
    repo_name = base_repo_name
    attempt = 0
    while attempt < 10:
        try:
            # Пытаемся создать репозиторий
            repo = user.create_repo(
                name=repo_name,
                description=f"AI-generated website: {site_name}",
                private=False,
                auto_init=False
            )
            break
        except GithubException as e:
            if e.status == 422:  # Имя уже занято
                attempt += 1
                repo_name = f"{base_repo_name}-{random.randint(1000, 9999)}"
            else:
                raise
    
    # Кодируем контент в base64 для GitHub API
    content_bytes = code_content.encode('utf-8')
    content_base64 = base64.b64encode(content_bytes).decode('utf-8')
    
    # Создаем файл index.html
    repo.create_file(
        path="index.html",
        message=f"✨ AI generated: {site_name}\n\nPollinations AI images included",
        content=content_base64,
        branch="main"
    )
    
    # Включаем GitHub Pages (через API v3)
    try:
        repo.edit(pages_source={"branch": "main", "path": "/"})
    except GithubException:
        # Pages может потребовать ручного включения в настройках репозитория
        pass
    
    return repo.html_url, repo.name


# =============================================================================
# FLASK ROUTES
# =============================================================================

@app.route('/')
def index():
    """Главная страница с формой создания сайта"""
    return render_template('index.html')


@app.route('/health')
def health():
    """Endpoint для проверки работоспособности (healthcheck Railway)"""
    return jsonify({"status": "ok", "service": "ai-website-builder"}), 200


@app.route('/generate', methods=['POST'])
def generate():
    """API endpoint для генерации сайта"""
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "Требуется JSON в теле запроса"}), 400
    
    prompt = data.get('prompt', '').strip()
    site_name = data.get('name', 'my-site').strip()
    
    # Валидация входных данных
    if not prompt:
        return jsonify({"error": "Поле 'prompt' обязательно и не может быть пустым"}), 400
    if len(prompt) > 2000:
        return jsonify({"error": "Описание слишком длинное (макс. 2000 символов)"}), 400
    if not site_name:
        site_name = "my-site"
    
    try:
        print(f"🎯 Новый запрос: '{prompt}' (проект: {site_name})")
        
        # Шаг 1: Генерация кода через ИИ
        print("🤖 Генерация HTML-кода...")
        html_code = generate_code(prompt)
        html_code = clean_html_code(html_code)
        
        if not html_code.strip().startswith('<'):
            raise RuntimeError("ИИ вернул некорректный HTML-код")
        
        # Шаг 2: Загрузка на GitHub
        print("📤 Загрузка на GitHub...")
        repo_url, repo_name = upload_to_github(html_code, site_name)
        
        # Формируем ссылку на GitHub Pages
        pages_url = f"https://{GITHUB_USER}.github.io/{repo_name}/"
        
        print(f"✅ Готово! Репозиторий: {repo_url}")
        
        return jsonify({
            "success": True,
            "message": "Сайт успешно создан!",
            "repo": repo_url,
            "preview": pages_url,
            "repo_name": repo_name,
            "code": html_code  # Для локального предпросмотра в iframe
        })
        
    except RuntimeError as e:
        print(f"❌ Ошибка бизнес-логики: {str(e)}")
        return jsonify({"error": str(e)}), 500
        
    except GithubException as e:
        print(f"❌ GitHub API ошибка: {str(e)}")
        if e.status == 401:
            return jsonify({"error": "Неверный GitHub токен. Проверьте переменную GITHUB_TOKEN"}), 500
        elif e.status == 403:
            return jsonify({"error": "Нет прав на создание репозиториев. Проверьте scope токена (нужен 'repo')"}), 500
        else:
            return jsonify({"error": f"GitHub ошибка: {e.data.get('message', str(e))}"}), 500
            
    except requests.exceptions.Timeout:
        return jsonify({"error": "Превышено время ожидания ответа от ИИ. Попробуйте ещё раз."}), 504
        
    except Exception as e:
        print(f"❌ Неожиданная ошибка: {type(e).__name__}: {str(e)}")
        return jsonify({"error": f"Внутренняя ошибка сервера: {str(e)}"}), 500


# =============================================================================
# ЗАПУСК ПРИЛОЖЕНИЯ
# =============================================================================

if __name__ == '__main__':
    # Railway.app задает порт через переменную окружения PORT
    # host="0.0.0.0" обязателен для доступа извне
    # debug=False в продакшене (включайте True только локально!)
    
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    
    print(f"🚀 Запуск сервера на порту {port} (debug={debug_mode})")
    
    app.run(
        host="0.0.0.0",
        port=port,
        debug=debug_mode,
        threaded=True  # Обработка нескольких запросов одновременно
    )
