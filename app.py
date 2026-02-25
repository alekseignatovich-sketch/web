import os
import re
import sys
import time
import requests
import base64
import random
import logging
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from github import Github, GithubException

# =============================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ (КРИТИЧНО ДЛЯ RAILWAY!)
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Загрузка .env только локально (на Railway переменные уже в окружении)
load_dotenv()

app = Flask(__name__)

# =============================================================================
# КОНФИГУРАЦИЯ С ПРОВЕРКАМИ
# =============================================================================
def get_env_config():
    """Безопасное получение конфигурации с детальным логированием"""
    config = {
        'github_token': os.getenv("GITHUB_TOKEN"),
        'groq_api_key': os.getenv("GROQ_API_KEY"),
        'github_user': os.getenv("GITHUB_USER")
    }
    
    missing = [k for k, v in config.items() if not v]
    if missing:
        logger.error(f"❌ Отсутствуют переменные окружения: {missing}")
        logger.error("💡 Проверьте вкладку Variables в панели Railway")
        return None
    
    # Маскируем токены в логах для безопасности
    logger.info("✅ Конфигурация загружена")
    logger.info(f"   GITHUB_USER: {config['github_user']}")
    logger.info(f"   GROQ_API_KEY: {config['groq_api_key'][:8]}...")
    logger.info(f"   GITHUB_TOKEN: {config['github_token'][:8]}...")
    
    return config

CONFIG = get_env_config()
if not CONFIG:
    logger.critical("🛑 Приложение не может запуститься без конфигурации")
    # Не выбрасываем исключение — пусть Flask запустится и вернет 500 на healthcheck
    # Это даст Railway понять, что сервис не готов

# =============================================================================
# ФУНКЦИИ
# =============================================================================

def generate_code(prompt: str) -> str:
    """Генерация HTML-кода через Groq API"""
    if not CONFIG:
        raise RuntimeError("Конфигурация не загружена")
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {CONFIG['groq_api_key']}",
        "Content-Type": "application/json"
    }
    
    system_prompt = """
    Ты эксперт-веб разработчик. Создай одностраничный сайт (HTML + CSS в <style>).
    
    🎨 ИЗОБРАЖЕНИЯ (Pollinations AI):
    - Формат: https://image.pollinations.ai/prompt/{описание_на_английском}
    - Пример: <img src="https://image.pollinations.ai/prompt/modern_office">
    - Только английский, слова через подчеркивание, без пробелов
    
    📦 ОТВЕТ:
    - ТОЛЬКО чистый HTML, начинай с <!DOCTYPE html>
    - Без markdown, без пояснений
    """

    payload = {
        "messages": [
            {"role": "system","content": system_prompt},
            {"role": "user","content": f"Создай сайт: {prompt}"}
        ],
        "model": "llama3-70b-8192",
        "temperature": 0.7,
        "max_tokens": 8192
    }

    logger.info(f"🤖 Запрос к Groq API для: {prompt[:50]}...")
    response = requests.post(url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    result = response.json()
    return result['choices'][0]['message']['content']


def clean_html_code(code: str) -> str:
    """Очистка кода от markdown и мусора"""
    code = re.sub(r'^```(?:html)?\s*', '', code, flags=re.MULTILINE)
    code = re.sub(r'\s*```$', '', code, flags=re.MULTILINE)
    code = code.strip()
    
    # Найти начало HTML если ИИ добавил текст перед кодом
    if not code.startswith('<'):
        match = re.search(r'(<!DOCTYPE[^>]*>|<html[^>]*>)', code, re.IGNORECASE)
        if match:
            code = code[match.start():]
    return code


def upload_to_github(code_content: str, site_name: str) -> tuple[str, str]:
    """Загрузка сайта на GitHub"""
    if not CONFIG:
        raise RuntimeError("Конфигурация не загружена")
        
    g = Github(CONFIG['github_token'])
    user = g.get_user()
    
    safe_name = re.sub(r'[^a-zA-Z0-9-]', '-', site_name.lower())
    safe_name = re.sub(r'-+', '-', safe_name).strip('-')
    base_repo_name = f"site-{safe_name}"
    
    # Уникальное имя репозитория
    repo_name = base_repo_name
    for attempt in range(10):
        try:
            repo = user.create_repo(
                name=repo_name,
                description=f"AI-generated: {site_name}",
                private=False,
                auto_init=False
            )
            logger.info(f"✅ Создан репозиторий: {repo_name}")
            break
        except GithubException as e:
            if e.status == 422:  # Имя занято
                repo_name = f"{base_repo_name}-{random.randint(1000, 9999)}"
            else:
                raise
    else:
        raise RuntimeError("Не удалось создать уникальное имя репозитория")
    
    # Загрузка файла
    content_b64 = base64.b64encode(code_content.encode('utf-8')).decode('utf-8')
    repo.create_file(
        path="index.html",
        message=f"✨ AI generated: {site_name}",
        content=content_b64,
        branch="main"
    )
    logger.info("✅ Файл index.html загружен")
    
    return repo.html_url, repo.name


# =============================================================================
# ROUTES
# =============================================================================

@app.route('/')
def index():
    """Главная страница"""
    logger.info("📄 Запрошена главная страница")
    return render_template('index.html')


@app.route('/health')
def health():
    """Healthcheck для Railway — должен возвращать 200"""
    # Проверяем, что конфигурация загружена
    if not CONFIG:
        logger.warning("⚠️ Healthcheck: конфигурация не загружена")
        return jsonify({"status": "unhealthy", "reason": "missing_config"}), 503
    
    # Проверяем доступность внешних API (опционально, можно закомментировать для скорости)
    try:
        # Быстрая проверка Groq API (без реального запроса)
        requests.get("https://api.groq.com", timeout=3)
    except:
        pass  # Не блокируем healthcheck, если API временно недоступен
    
    return jsonify({
        "status": "healthy",
        "service": "ai-website-builder",
        "timestamp": time.time()
    }), 200


@app.route('/generate', methods=['POST'])
def generate():
    """API генерации сайта"""
    logger.info("🎯 Получен запрос на генерацию")
    
    if not CONFIG:
        return jsonify({"error": "Сервер не настроен: отсутствуют переменные окружения"}), 503
    
    data = request.get_json(silent=True) or {}
    prompt = data.get('prompt', '').strip()
    site_name = data.get('name', 'my-site').strip() or 'my-site'
    
    if not prompt:
        return jsonify({"error": "Поле 'prompt' обязательно"}), 400
    if len(prompt) > 2000:
        return jsonify({"error": "Описание слишком длинное"}), 400
    
    try:
        logger.info(f"🤖 Генерация кода для: {site_name}")
        html_code = generate_code(prompt)
        html_code = clean_html_code(html_code)
        
        if not html_code.strip().startswith('<'):
            raise ValueError("ИИ вернул некорректный HTML")
        
        logger.info("📤 Загрузка на GitHub...")
        repo_url, repo_name = upload_to_github(html_code, site_name)
        pages_url = f"https://{CONFIG['github_user']}.github.io/{repo_name}/"
        
        logger.info(f"✅ Готово: {repo_url}")
        
        return jsonify({
            "success": True,
            "repo": repo_url,
            "preview": pages_url,
            "repo_name": repo_name,
            "code": html_code
        })
        
    except Exception as e:
        logger.error(f"❌ Ошибка генерации: {type(e).__name__}: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# =============================================================================
# ЗАПУСК
# =============================================================================

if __name__ == '__main__':
    # Railway задает PORT, локально используем 5000
    port = int(os.environ.get("PORT", 5000))
    # debug включаем только если явно задано FLASK_DEBUG=true
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    
    # КРИТИЧНО: host="0.0.0.0" чтобы принимать внешние соединения
    logger.info(f"🚀 Запуск Flask сервера")
    logger.info(f"   Host: 0.0.0.0")
    logger.info(f"   Port: {port}")
    logger.info(f"   Debug: {debug}")
    
    try:
        app.run(
            host="0.0.0.0",  # 🔥 Обязательно для Railway!
            port=port,
            debug=debug,
            threaded=True
        )
    except Exception as e:
        logger.critical(f"💥 Не удалось запустить сервер: {e}", exc_info=True)
        sys.exit(1)
