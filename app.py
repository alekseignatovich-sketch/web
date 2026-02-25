import os
import re
import sys
import time
import json
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

# =============================================================================
# ФУНКЦИИ
# =============================================================================

def generate_code(prompt: str) -> str:
    """Генерация HTML-кода через Groq API с fallback на несколько моделей"""
    if not CONFIG:
        raise RuntimeError("Конфигурация не загружена")
    
    # Валидация промпта
    if not prompt or len(prompt.strip()) < 5:
        raise ValueError("Описание сайта слишком короткое (мин. 5 символов)")
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {CONFIG['groq_api_key']}",
        "Content-Type": "application/json"
    }
    
    system_prompt = """
    You are an expert web developer. Create a complete, production-ready single-page website.
    
    REQUIREMENTS:
    1. Return ONLY valid HTML5 code, starting with <!DOCTYPE html>
    2. Include all CSS inside <style> tag in <head>
    3. Make it responsive, modern, and visually appealing
    4. Use Google Fonts (Inter or Roboto)
    
    IMAGES (Pollinations AI):
    - Use format: https://image.pollinations.ai/prompt/{english_description}
    - Example: <img src="https://image.pollinations.ai/prompt/modern_office_workspace" alt="Office">
    - Descriptions: English only, lowercase, underscores instead of spaces
    - Be specific: "happy_team_meeting" not just "people"
    
    OUTPUT FORMAT:
    - NO markdown blocks (```html)
    - NO explanations or comments outside the code
    - Start directly with <!DOCTYPE html>
    """

    # 🔥 Список моделей для попытки (fallback)
    models_to_try = [
        "llama-3.1-8b-instant",      # Быстрая и стабильная (рекомендуется)
        "llama3-70b-8192",           # Мощная, но может быть недоступна
        "gemma2-9b-it",              # Альтернатива
    ]
    
    last_error = None
    
    for model in models_to_try:
        try:
            logger.info(f"🤖 Попытка запроса к Groq: модель={model}")
            
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt.strip()},
                    {"role": "user", "content": f"Create a website: {prompt}"}
                ],
                "temperature": 0.7,
                "max_tokens": 8192,
                "top_p": 0.95
            }
            
            logger.debug(f"📦 Payload: model={model}, prompt_len={len(prompt)}")
            
            response = requests.post(
                url, 
                json=payload,
                headers=headers, 
                timeout=60
            )
            
            # 🚨 Детальная обработка ошибок
            if response.status_code != 200:
                error_text = response.text[:500]
                logger.error(f"❌ Groq API {response.status_code} [{model}]: {error_text}")
                
                if response.status_code == 401:
                    raise RuntimeError("❌ Неверный GROQ_API_KEY — проверьте переменную окружения")
                elif response.status_code == 400:
                    try:
                        error_json = response.json()
                        error_msg = error_json.get('error', {}).get('message', error_text)
                    except:
                        error_msg = error_text
                    raise RuntimeError(f"❌ Bad Request от Groq: {error_msg}")
                elif response.status_code == 403:
                    raise RuntimeError(f"❌ Модель {model} недоступна для вашего аккаунта")
                elif response.status_code == 429:
                    raise RuntimeError("⏳ Превышен лимит запросов Groq. Подождите 30 секунд")
                elif response.status_code >= 500:
                    raise RuntimeError(f"🔧 Серверная ошибка Groq: {response.status_code}")
                
                last_error = f"{model}: {response.status_code}"
                continue  # Пробуем следующую модель
            
            # ✅ Успешный ответ
            result = response.json()
            
            if not result.get('choices'):
                raise RuntimeError("📭 Groq вернул пустой ответ (нет choices)")
            
            content = result['choices'][0]['message']['content']
            logger.info(f"✅ Успешный ответ от {model}, длина кода: {len(content)} символов")
            return content
            
        except RuntimeError:
            raise  # Перехватываем наши ошибки и пробрасываем дальше
        except Exception as e:
            logger.warning(f"⚠️ Ошибка с моделью {model}: {type(e).__name__}: {str(e)[:100]}")
            last_error = f"{model}: {str(e)[:50]}"
            continue
    
    # 🚫 Все модели не сработали
    raise RuntimeError(f"Не удалось получить ответ от Groq API. Последняя ошибка: {last_error}")


def clean_html_code(code: str) -> str:
    """Очистка кода от markdown и мусора"""
    # Убираем markdown блоки
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
    
    # Санитизация имени для GitHub
    safe_name = re.sub(r'[^a-zA-Z0-9-]', '-', site_name.lower())
    safe_name = re.sub(r'-+', '-', safe_name).strip('-')
    base_repo_name = f"site-{safe_name}"
    
    # Уникальное имя репозитория
    repo_name = base_repo_name
    for attempt in range(10):
        try:
            repo = user.create_repo(
                name=repo_name,
                description=f"AI-generated website: {site_name}",
                private=False,
                auto_init=False
            )
            logger.info(f"✅ Создан репозиторий: {repo_name}")
            break
        except GithubException as e:
            if e.status == 422:  # Имя занято
                repo_name = f"{base_name}-{random.randint(1000, 9999)}"
            else:
                raise
    else:
        raise RuntimeError("Не удалось создать уникальное имя репозитория")
    
    # Загрузка файла index.html
    content_b64 = base64.b64encode(code_content.encode('utf-8')).decode('utf-8')
    repo.create_file(
        path="index.html",
        message=f"✨ AI generated: {site_name}\n\nPollinations AI images included",
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
    if not CONFIG:
        logger.warning("⚠️ Healthcheck: конфигурация не загружена")
        return jsonify({"status": "unhealthy", "reason": "missing_config"}), 503
    
    return jsonify({
        "status": "healthy",
        "service": "ai-website-builder",
        "timestamp": time.time()
    }), 200


@app.route('/generate', methods=['POST'])
def generate():
    """API endpoint для генерации сайта"""
    logger.info("🎯 Получен запрос на генерацию")
    
    if not CONFIG:
        return jsonify({"error": "Сервер не настроен: отсутствуют переменные окружения"}), 503
    
    data = request.get_json(silent=True) or {}
    prompt = data.get('prompt', '').strip()
    site_name = data.get('name', 'my-site').strip() or 'my-site'
    
    if not prompt:
        return jsonify({"error": "Поле 'prompt' обязательно"}), 400
    if len(prompt) > 2000:
        return jsonify({"error": "Описание слишком длинное (макс. 2000 символов)"}), 400
    
    try:
        logger.info(f"🤖 Генерация кода для: {site_name}")
        html_code = generate_code(prompt)
        html_code = clean_html_code(html_code)
        
        if not html_code.strip().startswith('<'):
            raise ValueError("ИИ вернул некорректный HTML-код")
        
        logger.info("📤 Загрузка на GitHub...")
        repo_url, repo_name = upload_to_github(html_code, site_name)
        pages_url = f"https://{CONFIG['github_user']}.github.io/{repo_name}/"
        
        logger.info(f"✅ Готово! Репозиторий: {repo_url}")
        
        return jsonify({
            "success": True,
            "message": "Сайт успешно создан!",
            "repo": repo_url,
            "preview": pages_url,
            "repo_name": repo_name,
            "code": html_code  # Для локального предпросмотра в iframe
        })
        
    except RuntimeError as e:
        logger.error(f"❌ Ошибка бизнес-логики: {str(e)}")
        return jsonify({"error": str(e)}), 500
        
    except GithubException as e:
        logger.error(f"❌ GitHub API ошибка: {str(e)}")
        if e.status == 401:
            return jsonify({"error": "Неверный GitHub токен. Проверьте GITHUB_TOKEN"}), 500
        elif e.status == 403:
            return jsonify({"error": "Нет прав на создание репозиториев (нужен scope 'repo')"}), 500
        else:
            return jsonify({"error": f"GitHub ошибка: {e.data.get('message', str(e))}"}), 500
            
    except ValueError as e:
        logger.error(f"❌ Ошибка валидации: {str(e)}")
        return jsonify({"error": str(e)}), 400
        
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка: {type(e).__name__}: {str(e)}", exc_info=True)
        return jsonify({"error": f"Внутренняя ошибка сервера: {str(e)}"}), 500


# =============================================================================
# ЗАПУСК ПРИЛОЖЕНИЯ
# =============================================================================

if __name__ == '__main__':
    # Railway задает порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    
    logger.info("🚀 Запуск Flask сервера")
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
