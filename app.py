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
from github.Auth import Token  # 🔥 Современная аутентификация

# =============================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

load_dotenv()
app = Flask(__name__)

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================
def get_env_config():
    config = {
        'github_token': os.getenv("GITHUB_TOKEN"),
        'groq_api_key': os.getenv("GROQ_API_KEY"),
        'github_user': os.getenv("GITHUB_USER")
    }
    missing = [k for k, v in config.items() if not v]
    if missing:
        logger.error(f"❌ Отсутствуют переменные окружения: {missing}")
        return None
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
    if not CONFIG:
        raise RuntimeError("Конфигурация не загружена")
    if not prompt or len(prompt.strip()) < 5:
        raise ValueError("Описание сайта слишком короткое (мин. 5 символов)")
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {CONFIG['groq_api_key']}",
        "Content-Type": "application/json"
    }
    
    system_prompt = """
    You are an expert web developer. Create a complete, production-ready single-page website.
    Return ONLY valid HTML5 code starting with <!DOCTYPE html>.
    Include all CSS inside <style> tag in <head>.
    Use Google Fonts (Inter or Roboto), make it responsive.
    
    IMAGES (Pollinations AI):
    - Format: https://image.pollinations.ai/prompt/{english_description}
    - Example: <img src="https://image.pollinations.ai/prompt/modern_office">
    - English only, lowercase, underscores for spaces
    
    NO markdown blocks, NO explanations - just pure HTML code.
    """

    models_to_try = ["llama-3.1-8b-instant", "llama3-70b-8192", "gemma2-9b-it"]
    last_error = None
    
    for model in models_to_try:
        try:
            logger.info(f"🤖 Запрос к Groq: {model}")
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
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            
            if response.status_code != 200:
                error_text = response.text[:500]
                logger.error(f"❌ Groq {response.status_code} [{model}]: {error_text}")
                if response.status_code == 401:
                    raise RuntimeError("❌ Неверный GROQ_API_KEY")
                elif response.status_code == 400:
                    try:
                        error_json = response.json()
                        error_msg = error_json.get('error', {}).get('message', error_text)
                    except:
                        error_msg = error_text
                    raise RuntimeError(f"❌ Bad Request: {error_msg}")
                elif response.status_code == 429:
                    raise RuntimeError("⏳ Лимит запросов Groq")
                elif response.status_code >= 500:
                    raise RuntimeError(f"🔧 Ошибка сервера Groq: {response.status_code}")
                last_error = f"{model}: {response.status_code}"
                continue
            
            result = response.json()
            if not result.get('choices'):
                raise RuntimeError("📭 Пустой ответ от Groq")
            content = result['choices'][0]['message']['content']
            logger.info(f"✅ Ответ от {model}, длина: {len(content)}")
            return content
            
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"⚠️ Ошибка {model}: {type(e).__name__}: {str(e)[:100]}")
            last_error = f"{model}: {str(e)[:50]}"
            continue
    
    raise RuntimeError(f"Не удалось получить ответ от Groq. Последняя: {last_error}")


def clean_html_code(code: str) -> str:
    code = re.sub(r'^```(?:html)?\s*', '', code, flags=re.MULTILINE)
    code = re.sub(r'\s*```$', '', code, flags=re.MULTILINE)
    code = code.strip()
    if not code.startswith('<'):
        match = re.search(r'(<!DOCTYPE[^>]*>|<html[^>]*>)', code, re.IGNORECASE)
        if match:
            code = code[match.start():]
    return code


def upload_to_github(code_content: str, site_name: str) -> tuple[str, str]:
    if not CONFIG:
        raise RuntimeError("Конфигурация не загружена")
    
    # 🔥 Современная аутентификация
    auth = Token(CONFIG['github_token'])
    g = Github(auth=auth)
    user = g.get_user()
    
    safe_name = re.sub(r'[^a-zA-Z0-9-]', '-', site_name.lower())
    safe_name = re.sub(r'-+', '-', safe_name).strip('-')
    base_repo_name = f"site-{safe_name}"
    
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
            if e.status == 422:
                repo_name = f"{base_repo_name}-{random.randint(1000, 9999)}"
            else:
                raise
    else:
        raise RuntimeError("Не удалось создать имя репозитория")
    
    # Загрузка файла
    logger.info(f"📤 Загрузка index.html ({len(code_content)} символов)...")
    try:
        repo.create_file(
            path="index.html",
            message=f"✨ AI generated: {site_name}",
            content=code_content,  # ← Строка, PyGithub закодирует сам
            branch="main"
        )
        logger.info("✅ Файл загружен")
    except GithubException as e:
        logger.error(f"❌ Ошибка загрузки: {e}")
        raise RuntimeError(f"Не удалось загрузить файл: {e}")
    
    # 🔥 Включение GitHub Pages через прямой API-запрос
    try:
        pages_url = f"https://api.github.com/repos/{CONFIG['github_user']}/{repo_name}/pages"
        headers = {
            "Authorization": f"Bearer {CONFIG['github_token']}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }
        payload = {"source": {"branch": "main", "path": "/"}}
        
        response = requests.post(pages_url, headers=headers, json=payload, timeout=30)
        if response.status_code in [200, 201, 202]:
            logger.info("✅ GitHub Pages включён")
        elif response.status_code == 409:
            logger.warning("⚠️ Pages уже включён")
        elif response.status_code == 403:
            logger.warning("⚠️ Нет прав на включение Pages")
        else:
            logger.warning(f"⚠️ Pages: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка Pages: {type(e).__name__}: {str(e)[:100]}")
    
    return repo.html_url, repo.name


# =============================================================================
# ROUTES
# =============================================================================

@app.route('/')
def index():
    logger.info("📄 Главная страница")
    return render_template('index.html')


@app.route('/health')
def health():
    if not CONFIG:
        return jsonify({"status": "unhealthy", "reason": "missing_config"}), 503
    return jsonify({"status": "healthy", "service": "ai-website-builder", "timestamp": time.time()}), 200


@app.route('/generate', methods=['POST'])
def generate():
    logger.info("🎯 Запрос на генерацию")
    if not CONFIG:
        return jsonify({"error": "Сервер не настроен"}), 503
    
    data = request.get_json(silent=True) or {}
    prompt = data.get('prompt', '').strip()
    site_name = data.get('name', 'my-site').strip() or 'my-site'
    
    if not prompt:
        return jsonify({"error": "Поле 'prompt' обязательно"}), 400
    if len(prompt) > 2000:
        return jsonify({"error": "Описание слишком длинное"}), 400
    
    try:
        logger.info(f"🤖 Генерация: {site_name}")
        html_code = generate_code(prompt)
        html_code = clean_html_code(html_code)
        if not html_code.strip().startswith('<'):
            raise ValueError("Некорректный HTML от ИИ")
        
        logger.info("📤 Загрузка на GitHub...")
        repo_url, repo_name = upload_to_github(html_code, site_name)
        pages_url = f"https://{CONFIG['github_user']}.github.io/{repo_name}/"
        
        logger.info(f"✅ Готово: {repo_url}")
        return jsonify({
            "success": True,
            "message": "Сайт создан!",
            "repo": repo_url,
            "preview": pages_url,
            "repo_name": repo_name,
            "code": html_code
        })
        
    except RuntimeError as e:
        logger.error(f"❌ Ошибка: {str(e)}")
        return jsonify({"error": str(e)}), 500
    except GithubException as e:
        logger.error(f"❌ GitHub: {str(e)}")
        if e.status == 401:
            return jsonify({"error": "Неверный GitHub токен"}), 500
        elif e.status == 403:
            return jsonify({"error": "Нет прав (нужен scope 'repo')"}), 500
        return jsonify({"error": f"GitHub: {e.data.get('message', str(e))}"}), 500
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"❌ Неожиданная: {type(e).__name__}: {str(e)}", exc_info=True)
        return jsonify({"error": f"Ошибка сервера: {str(e)}"}), 500


# =============================================================================
# ЗАПУСК
# =============================================================================

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    logger.info(f"🚀 Запуск: 0.0.0.0:{port} (debug={debug})")
    try:
        app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
    except Exception as e:
        logger.critical(f"💥 Ошибка запуска: {e}", exc_info=True)
        sys.exit(1)
