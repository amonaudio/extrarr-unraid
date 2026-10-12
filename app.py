import os
import requests
import subprocess
import glob
import json
import re
from flask import Flask, render_template, jsonify, request, send_file, make_response

app = Flask(__name__)

CONFIG_FILE = "/config/config.json"

def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"ERRORE LETTURA CONFIG: {str(e)}", flush=True)
    return {
        "radarr_url": "", "radarr_api_key": "",
        "sonarr_url": "", "sonarr_api_key": "",
        "fanart_api_key": "",
        "tmdb_api_key": "",
        "path_map": []
    }

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        return True
    except Exception as e:
        print(f"ERRORE SALVATAGGIO CONFIG: {str(e)}", flush=True)
        return False

def resolve_local_path(media_path, path_map_list):
    for mapping in path_map_list:
        docker_path = mapping.get("docker", "")
        local_path = mapping.get("local", "")
        if docker_path and media_path.startswith(docker_path):
            return media_path.replace(docker_path, local_path, 1)
    return media_path

def validate_local_path(local_path):
    if not local_path.startswith('/mnt/'):
        return False, f"Percorso non mappato: {local_path}."
    return True, ""

def get_nfo_filename(media_type, local_path):
    if media_type == "sonarr":
        return "tvshow.nfo"
    else:
        return f"{os.path.basename(local_path)}.nfo"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/favicon.png')
def favicon():
    file_path = os.path.join('/config', 'favicon.png')
    if os.path.exists(file_path):
        return send_file(file_path, mimetype='image/png')
    return jsonify({"error": "Icon not found"}), 404

@app.route('/api/settings', methods=['GET', 'POST'])
def settings_api():
    if request.method == 'GET':
        return jsonify(load_config())
    else:
        if save_config(request.get_json()):
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "Impossibile salvare"}), 500

@app.route('/api/browse_dirs', methods=['POST'])
def browse_dirs():
    req_path = request.get_json().get('path', '/mnt/user/')
    if not os.path.exists(req_path): req_path = '/mnt/user/'
    if not req_path.endswith('/'): req_path += '/'
    dirs = []
    try:
        for name in os.listdir(req_path):
            if os.path.isdir(os.path.join(req_path, name)):
                dirs.append({"name": name, "path": os.path.join(req_path, name)})
        return jsonify({"current": req_path, "dirs": dirs})
    except Exception as e:
        return jsonify({"current": req_path, "dirs": [], "error": str(e)})

@app.route('/api/root_folders/<media_type>', methods=['GET'])
def get_root_folders(media_type):
    config = load_config()
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    if not config.get(api_key):
        return jsonify([])
    headers = {"X-Api-Key": config.get(api_key, "")}
    try:
        r = requests.get(f"{config[url_key]}/api/v3/rootFolder", headers=headers, timeout=10)
        r.raise_for_status()
        folders = [rf['path'] for rf in r.json()]
        return jsonify(folders)
    except:
        return jsonify([])

@app.route('/api/media/<media_type>')
def get_media(media_type):
    config = load_config()
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    if not config.get(api_key):
        return jsonify({"error": f"API Key per {media_type} mancante"}), 401
        
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}", headers=headers, timeout=15)
        r.raise_for_status()
        items = r.json()
    except Exception as e:
        return jsonify({"error": f"Errore connessione {media_type}: {str(e)}"}), 500

    result = []
    for m in items:
        radarr_path = m.get('path', '')
        local_path = resolve_local_path(radarr_path, config.get("path_map", []))
        
        is_mapped = local_path.startswith('/mnt/')
        folder_exists = is_mapped and os.path.exists(local_path)
        
        has_theme = False
        has_artwork = False
        has_trailer = False
        
        if folder_exists:
            try:
                files_in_dir = os.listdir(local_path)
                for f in files_in_dir:
                    if f.lower() == 'theme.mp3':
                        has_theme = True
                        break
                        
                art_keywords = ['clearlogo', 'clearart', 'banner', 'landscape', 'discart', 'keyart', 'logo']
                for f in files_in_dir:
                    fl = f.lower()
                    if fl.endswith(('.jpg', '.png', '.jpeg')):
                        if any(kw in fl for kw in art_keywords):
                            has_artwork = True
                            break
                            
                nfo_filename = get_nfo_filename(media_type, local_path)
                nfo_file = os.path.join(local_path, nfo_filename)
                if os.path.exists(nfo_file):
                    try:
                        with open(nfo_file, 'r', encoding='utf-8') as f:
                            if '<trailer>' in f.read().lower():
                                has_trailer = True
                    except:
                        pass
            except Exception as e:
                print(f"Errore lettura cartella {local_path}: {e}", flush=True)

        poster_url = ""
        for img in m.get('images', []):
            if img.get('coverType') == 'poster': poster_url = img.get('remoteUrl', ''); break
        
        title = m.get('title', 'Sconosciuto')
        year = m.get('year', 'N/A')
        if media_type == "sonarr":
            year = m.get('year') or (m.get('firstAired', 'N/A')[:4] if m.get('firstAired') else 'N/A')

        # NUOVO CAMPO: Data Aggiunta
        date_added = m.get('added', '')

        result.append({
            "id": m['id'], "title": title, "year": year,
            "poster": poster_url, 
            "has_theme": has_theme,
            "has_artwork": has_artwork,
            "has_trailer": has_trailer,
            "is_mapped": is_mapped,
            "folder_exists": folder_exists, 
            "root_folder": radarr_path,
            "local_path": local_path,
            "date_added": date_added
        })
    result.sort(key=lambda x: x['title'])
    return jsonify(result)

@app.route('/api/search_media/<media_type>/<int:media_id>/<search_type>', methods=['GET'])
def search_media(media_type, media_id, search_type):
    config = load_config()
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}/{media_id}", headers=headers, timeout=10)
        media = r.json()
    except: 
        return jsonify({"status": "error", "message": "Errore API"}), 500
    
    lang = request.args.get('lang', 'en')
    
    if search_type == "theme":
        query = f"ytsearch10:{media['title']} theme song"
    else:
        if lang == 'it':
            query = f"ytsearch10:{media['title']} trailer ita"
        else:
            query = f"ytsearch10:{media['title']} trailer"
        
    cmd = ['yt-dlp', '--flat-playlist', '--dump-json', '--quiet', '--no-warnings', '--user-agent', 'Mozilla/5.0', '--force-ipv4', query]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    videos = []
    for l in result.stdout.strip().split('\n'):
        if l:
            data = json.loads(l)
            vid = data.get('id')
            if vid: videos.append({"title": data.get('title'), "url": f"https://www.youtube.com/watch?v={vid}", "video_id": vid})
    return jsonify(videos)

@app.route('/api/search_youtube', methods=['POST'])
def search_youtube():
    q = request.get_json().get('query')
    if not q: return jsonify([])
    cmd = ['yt-dlp', '--flat-playlist', '--dump-json', '--quiet', '--no-warnings', '--user-agent', 'Mozilla/5.0', '--force-ipv4', f"ytsearch10:{q}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    videos = []
    for l in result.stdout.strip().split('\n'):
        if l:
            data = json.loads(l)
            vid = data.get('id')
            if vid: videos.append({"title": data.get('title'), "url": f"https://www.youtube.com/watch?v={vid}", "video_id": vid})
    return jsonify(videos)

@app.route('/api/get_trailer/<media_type>/<int:media_id>', methods=['GET'])
def get_trailer(media_type, media_id):
    config = load_config()
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}/{media_id}", headers=headers, timeout=10)
        media = r.json()
    except: 
        return jsonify({"status": "error", "message": "Errore API"}), 500

    local_path = resolve_local_path(media.get('path', ''), config.get("path_map", []))
    nfo_filename = get_nfo_filename(media_type, local_path)
    nfo_file = os.path.join(local_path, nfo_filename)
    
    if not os.path.exists(nfo_file):
        return jsonify({"status": "error", "message": "File NFO non trovato"}), 404
        
    with open(nfo_file, 'r', encoding='utf-8') as f:
        content = f.read()
    match = re.search(r'<trailer>.*?video_id=([a-zA-Z0-9_-]{11})</trailer>', content)
    if not match:
        match = re.search(r'<trailer>(?:https?://www\.youtube\.com/watch\?v=|https?://youtu\.be/)([a-zA-Z0-9_-]{11})</trailer>', content)
    if match:
        return jsonify({"status": "success", "video_id": match.group(1)})
    return jsonify({"status": "error", "message": "Nessun trailer trovato nell'NFO"}), 404

@app.route('/api/download_media/<media_type>/<int:media_id>', methods=['POST'])
def download_media_theme(media_type, media_id):
    config = load_config()
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}/{media_id}", headers=headers, timeout=10)
        media = r.json()
    except: 
        return jsonify({"status": "error", "message": "Errore API"}), 500
        
    local_path = resolve_local_path(media['path'], config.get("path_map", []))
    valid, err_msg = validate_local_path(local_path)
    if not valid:
        return jsonify({"status": "error", "message": err_msg}), 400
    
    if not os.path.exists(local_path): os.makedirs(local_path, exist_ok=True)
    theme_mp3 = os.path.join(local_path, "theme.mp3")
    out_tmpl = os.path.join(local_path, "theme.%(ext)s")
    target_url = request.get_json().get('url')
    search_query = target_url if target_url else f"ytsearch1:{media['title']} theme song"
    if os.path.exists(theme_mp3): os.remove(theme_mp3)
    
    cmd = ['yt-dlp', '-x', '--audio-format', 'mp3', '--audio-quality', '0', '-o', out_tmpl, '--no-playlist', '--quiet', '--no-warnings', '--no-write-thumbnail', '--no-write-info-json', '--user-agent', 'Mozilla/5.0', '--force-ipv4', search_query]
    subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    
    if os.path.exists(theme_mp3):
        [os.remove(sc) for sc in glob.glob(os.path.join(local_path, "theme.*")) if sc != theme_mp3]
        return jsonify({"status": "success", "message": f"Tema scaricato per {media['title']}"})
    files = glob.glob(os.path.join(local_path, "theme.*"))
    if files:
        os.rename(files[0], theme_mp3)
        [os.remove(sc) for sc in glob.glob(os.path.join(local_path, "theme.*")) if sc != theme_mp3]
        return jsonify({"status": "success", "message": f"Tema scaricato per {media['title']}"})
    return jsonify({"status": "error", "message": "Errore yt-dlp"}), 500

@app.route('/api/set_trailer/<media_type>/<int:media_id>', methods=['POST'])
def set_trailer(media_type, media_id):
    config = load_config()
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}/{media_id}", headers=headers, timeout=10)
        media = r.json()
    except: 
        return jsonify({"status": "error", "message": "Errore API"}), 500

    local_path = resolve_local_path(media.get('path', ''), config.get("path_map", []))
    valid, err_msg = validate_local_path(local_path)
    if not valid:
        return jsonify({"status": "error", "message": err_msg}), 400

    if not os.path.exists(local_path): os.makedirs(local_path, exist_ok=True)
    
    nfo_filename = get_nfo_filename(media_type, local_path)
    nfo_file = os.path.join(local_path, nfo_filename)
    
    video_url = request.get_json().get('url')
    match = re.search(r'(?:v=|youtu\.be\/)([a-zA-Z0-9_-]{11})', video_url)
    if not match:
        return jsonify({"status": "error", "message": "URL YouTube non valido"}), 400
    video_id = match.group(1)
    
    trailer_line = f"  <trailer>plugin://plugin.video.youtube/play/?video_id={video_id}</trailer>\n"
    content = ""
    if os.path.exists(nfo_file):
        with open(nfo_file, 'r', encoding='utf-8') as f:
            content = f.read()
        if '<trailer>' in content:
            content = re.sub(r'<trailer>.*?</trailer>', f'<trailer>plugin://plugin.video.youtube/play/?video_id={video_id}</trailer>', content)
        else:
            root_tag = "movie" if media_type == "radarr" else "tvshow"
            if f'</{root_tag}>' in content:
                content = content.replace(f'</{root_tag}>', f'{trailer_line}</{root_tag}>')
            else:
                content += trailer_line
    else:
        root_tag = "movie" if media_type == "radarr" else "tvshow"
        content = f"<{root_tag}>\n{trailer_line}</{root_tag}>\n"
        
    with open(nfo_file, 'w', encoding='utf-8') as f:
        f.write(content)
    return jsonify({"status": "success", "message": f"Trailer salvato in {nfo_filename}"})

@app.route('/api/download_artwork/<media_type>/<int:media_id>', methods=['POST'])
def download_artwork(media_type, media_id):
    config = load_config()
    fanart_key = config.get("fanart_api_key", "")
    if not fanart_key:
        return jsonify({"status": "error", "message": "Fanart API Key mancante"}), 400
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}/{media_id}", headers=headers, timeout=10)
        media = r.json()
    except: 
        return jsonify({"status": "error", "message": "Errore API"}), 500

    local_path = resolve_local_path(media.get('path', ''), config.get("path_map", []))
    valid, err_msg = validate_local_path(local_path)
    if not valid:
        return jsonify({"status": "error", "message": err_msg}), 400
    if not os.path.exists(local_path): os.makedirs(local_path, exist_ok=True)
    folder_name = os.path.basename(local_path)

    art_id = media.get('tmdbId') if media_type == "radarr" else media.get('tvdbId')
    if not art_id:
        return jsonify({"status": "error", "message": "ID (TMDB/TVDB) mancante"}), 400

    ft_url = f"https://webservice.fanart.tv/v3/{'movies' if media_type == 'radarr' else 'tv'}/{art_id}?api_key={fanart_key}"
    try:
        r = requests.get(ft_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        data = r.json()
    except:
        return jsonify({"status": "error", "message": "Errore API Fanart.tv"}), 500

    art_types = {
        'hdmovielogo': 'clearlogo.png', 'movielogo': 'clearlogo.png',
        'hdmovieclearart': 'clearart.png', 'movieart': 'clearart.png',
        'moviebanner': 'banner.jpg', 'moviedisc': 'discart.png', 'moviethumb': 'landscape.jpg'
    }
    if media_type == "sonarr":
        art_types = {
            'hdtvlogo': 'clearlogo.png', 'clearlogo': 'clearlogo.png',
            'hdclearart': 'clearart.png', 'clearart': 'clearart.png',
            'tvbanner': 'banner.jpg', 'tvthumb': 'landscape.jpg'
        }

    downloaded = []
    for ft_type, filename in art_types.items():
        items = data.get(ft_type, [])
        if items:
            items.sort(key=lambda x: int(x.get('likes', '0')), reverse=True)
            img_url = items[0].get('url')
            if img_url:
                save_path = os.path.join(local_path, f"{folder_name}-{filename}")
                if not os.path.exists(save_path):
                    try:
                        img_data = requests.get(img_url, timeout=15).content
                        with open(save_path, 'wb') as f: f.write(img_data)
                        downloaded.append(filename)
                    except: pass

    if downloaded:
        return jsonify({"status": "success", "message": f"Scaricate {len(downloaded)} artwork: {', '.join(downloaded)}"})
    return jsonify({"status": "success", "message": "Nessuna artwork extra trovata"})

@app.route('/api/search_artwork_online/<media_type>/<int:media_id>', methods=['GET'])
def search_artwork_online(media_type, media_id):
    config = load_config()
    fanart_key = config.get("fanart_api_key", "")
    if not fanart_key:
        return jsonify({"status": "error", "message": "Fanart API Key mancante"}), 400
        
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}/{media_id}", headers=headers, timeout=10)
        media = r.json()
    except: 
        return jsonify({"status": "error", "message": "Errore API"}), 500

    art_id = media.get('tmdbId') if media_type == "radarr" else media.get('tvdbId')
    if not art_id:
        return jsonify({"status": "error", "message": "ID (TMDB/TVDB) mancante"}), 400

    ft_url = f"https://webservice.fanart.tv/v3/{'movies' if media_type == 'radarr' else 'tv'}/{art_id}?api_key={fanart_key}"
    try:
        r = requests.get(ft_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        data = r.json()
    except:
        return jsonify({"status": "error", "message": "Errore API Fanart.tv"}), 500

    fanart_mapping = {
        'hdmovielogo': 'clearlogo', 'movielogo': 'clearlogo',
        'hdmovieclearart': 'clearart', 'movieart': 'clearart',
        'moviebanner': 'banner', 'moviedisc': 'discart', 'moviethumb': 'landscape',
        'hdtvlogo': 'clearlogo', 'clearlogo': 'clearlogo',
        'hdclearart': 'clearart', 'clearart': 'clearart',
        'tvbanner': 'banner', 'tvthumb': 'landscape'
    }

    result = []
    for ft_type, our_type in fanart_mapping.items():
        items = data.get(ft_type, [])
        for item in items:
            img_url = item.get('url')
            if img_url:
                ext = os.path.splitext(img_url.split('?')[0])[1]
                if not ext: ext = '.png'
                result.append({
                    "our_type": our_type,
                    "ext": ext.lstrip('.'),
                    "url": img_url,
                    "lang": item.get('lang', 'N/D'),
                    "likes": item.get('likes', '0')
                })
    return jsonify(result)

@app.route('/api/search_tmdb_artwork/<media_type>/<int:media_id>', methods=['GET'])
def search_tmdb_artwork(media_type, media_id):
    config = load_config()
    tmdb_key = config.get("tmdb_api_key", "")
    if not tmdb_key:
        return jsonify({"status": "error", "message": "TMDB API Key mancante"}), 400
    
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}/{media_id}", headers=headers, timeout=10)
        media = r.json()
    except: 
        return jsonify({"status": "error", "message": "Errore API"}), 500

    tmdb_id = media.get('tmdbId')
    if not tmdb_id:
        if media_type == "sonarr":
            tvdb_id = media.get('tvdbId')
            if tvdb_id:
                find_url = f"https://api.themoviedb.org/3/find/{tvdb_id}?api_key={tmdb_key}&external_source=tvdb_id"
                try:
                    find_r = requests.get(find_url, timeout=10)
                    find_data = find_r.json()
                    tv_results = find_data.get('tv_results', [])
                    if tv_results:
                        tmdb_id = tv_results[0].get('id')
                except:
                    pass
        if not tmdb_id:
            return jsonify({"status": "error", "message": "TMDB ID non disponibile"}), 400

    if media_type == "radarr":
        tmdb_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/images?api_key={tmdb_key}"
    else:
        tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/images?api_key={tmdb_key}"
        
    try:
        r = requests.get(tmdb_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        data = r.json()
    except:
        return jsonify({"status": "error", "message": "Errore API TMDB"}), 500

    result = []
    
    for logo in data.get('logos', []):
        file_path = logo.get('file_path')
        if file_path:
            result.append({
                "our_type": "clearlogo",
                "ext": "png",
                "url": f"https://image.tmdb.org/t/p/original{file_path}",
                "lang": logo.get('iso_639_1') or 'N/D',
                "likes": str(logo.get('vote_average', 0))
            })
    
    for backdrop in data.get('backdrops', []):
        file_path = backdrop.get('file_path')
        if file_path:
            result.append({
                "our_type": "landscape",
                "ext": "jpg",
                "url": f"https://image.tmdb.org/t/p/original{file_path}",
                "lang": backdrop.get('iso_639_1') or 'N/D',
                "likes": str(backdrop.get('vote_average', 0))
            })
    
    for poster in data.get('posters', []):
        file_path = poster.get('file_path')
        if file_path:
            result.append({
                "our_type": "poster",
                "ext": "jpg",
                "url": f"https://image.tmdb.org/t/p/original{file_path}",
                "lang": poster.get('iso_639_1') or 'N/D',
                "likes": str(poster.get('vote_average', 0))
            })
    
    return jsonify(result)

@app.route('/api/download_specific_artwork/<media_type>/<int:media_id>', methods=['POST'])
def download_specific_artwork(media_type, media_id):
    config = load_config()
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}/{media_id}", headers=headers, timeout=10)
        media = r.json()
    except: 
        return jsonify({"status": "error", "message": "Errore API"}), 500

    local_path = resolve_local_path(media.get('path', ''), config.get("path_map", []))
    valid, err_msg = validate_local_path(local_path)
    if not valid:
        return jsonify({"status": "error", "message": err_msg}), 400
        
    if not os.path.exists(local_path): os.makedirs(local_path, exist_ok=True)
    folder_name = os.path.basename(local_path)

    data = request.get_json()
    img_url = data.get('url')
    our_type = data.get('our_type')
    ext = data.get('ext', 'png')
    
    if not img_url or not our_type:
        return jsonify({"status": "error", "message": "Dati mancanti"}), 400
        
    save_path = os.path.join(local_path, f"{folder_name}-{our_type}.{ext}")
    
    try:
        img_data = requests.get(img_url, timeout=15).content
        with open(save_path, 'wb') as f: 
            f.write(img_data)
        return jsonify({"status": "success", "message": f"Artwork {our_type} scaricato/sostituito"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/artwork_files/<media_type>/<int:media_id>', methods=['GET'])
def get_artwork_files(media_type, media_id):
    config = load_config()
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}/{media_id}", headers=headers, timeout=10)
        media = r.json()
    except: 
        return jsonify([])
        
    local_path = resolve_local_path(media.get('path', ''), config.get("path_map", []))
    images = []
    if os.path.exists(local_path):
        for f in os.listdir(local_path):
            if f.lower().endswith(('.jpg', '.png', '.jpeg')):
                images.append(f)
    return jsonify(images)

@app.route('/api/artwork_image/<media_type>/<int:media_id>/<path:filename>', methods=['GET'])
def get_artwork_image(media_type, media_id, filename):
    config = load_config()
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}/{media_id}", headers=headers, timeout=10)
        media = r.json()
    except: 
        return jsonify({"error": "Errore API"}), 500
        
    local_path = resolve_local_path(media.get('path', ''), config.get("path_map", []))
    file_path = os.path.join(local_path, filename)
    if os.path.exists(file_path):
        return send_file(file_path, mimetype="image/jpeg")
    return jsonify({"error": "File not found"}), 404

@app.route('/api/audio_media/<media_type>/<int:media_id>')
def get_audio_media(media_type, media_id):
    config = load_config()
    url_key = f"{media_type}_url"
    api_key = f"{media_type}_api_key"
    headers = {"X-Api-Key": config.get(api_key, "")}
    api_path = "movie" if media_type == "radarr" else "series"
    try:
        r = requests.get(f"{config[url_key]}/api/v3/{api_path}/{media_id}", headers=headers, timeout=10)
        media = r.json()
    except: 
        return jsonify({"error": "Errore API"}), 500
        
    local_path = resolve_local_path(media.get('path', ''), config.get("path_map", []))
    theme_path = os.path.join(local_path, "theme.mp3")
    if os.path.exists(theme_path):
        resp = make_response(send_file(theme_path, mimetype="audio/mpeg"))
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    return jsonify({"error": "File not found"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
