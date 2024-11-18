
from flask import Flask, request, jsonify, send_from_directory, render_template, redirect, session, url_for, Blueprint
import requests
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
import os
import ffmpeg
import base64
import json
import random
import time
from flask_cors import CORS  # Import Flask-CORS
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from cachetools import TTLCache
spotify_token_cache = TTLCache(maxsize=1, ttl=3600)



# Create a blueprint
songRoutes = Flask(__name__)  
CORS(songRoutes, resources={r"/*": {"origins": "*"}})  # Allow all origins

# Define the directory to save downloads
DOWNLOAD_FOLDER = os.path.join(songRoutes.root_path, 'downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Load environment variables
load_dotenv()

# Load credentials and configuration from environment variables
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URL')

GITHUB_API_URL = 'https://api.github.com'
MY_GITHUB_TOKEN = os.getenv('MY_GITHUB_TOKEN_FOR_POOKIEFY')
GITHUB_REPO = "pavansweb/pookiefy-song-storage"
SPOTIFY_API_URL = "https://spotify-downloader9.p.rapidapi.com/downloadSong"
SPOTIFY_API_KEY = "f136c0aa68mshb36ffbc4dc13eafp117fecjsn56310af5bb86"

# Parse and randomly select a RapidAPI key
RAPIDAPI_KEYS = os.getenv('RAPIDAPI_KEYS').split(',')
RAPIDAPI_KEY = random.choice(RAPIDAPI_KEYS)

spotify_token_cache = TTLCache(maxsize=1, ttl=3600)

def get_spotify_token():
    if 'token' in spotify_token_cache:
        return spotify_token_cache['token']

    token_response = requests.post('https://accounts.spotify.com/api/token', data={
        'grant_type': 'client_credentials'
    }, headers={
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': 'Basic ' + base64.b64encode(f'{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}'.encode()).decode()
    })

    token_data = token_response.json()
    token = token_data.get('access_token')
    if token:
        spotify_token_cache['token'] = token  # Cache the token
    return token


@songRoutes.route('/search-spotify-song', methods=['POST'])
def search_spotify_song():
    try:
        search_term = request.json.get('searchTerm', '').strip()

        if not search_term:
            return jsonify({'success': False, 'error': 'No search term provided'}), 400

        token = get_spotify_token()
        if not token:
            return jsonify({'success': False, 'error': 'Unable to fetch Spotify token'}), 500

        search_response = requests.get(f'https://api.spotify.com/v1/search?q={search_term}&type=track', headers={
            'Authorization': f'Bearer {token}'
        })
        search_data = search_response.json()

        unique_songs = []
        song_set = set()

        for song in search_data.get('tracks', {}).get('items', []):
            song_identifier = f"{song['name']}-{song['artists'][0]['name']}"
            if song_identifier not in song_set:
                song_set.add(song_identifier)
                unique_songs.append(song)

        return jsonify({
            'success': True,
            'songs': unique_songs
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500



def convert_mp3_to_ogg(input_filepath, output_filepath):
    try:
        ffmpeg.input(input_filepath).output(output_filepath).run()
        return True
    except Exception as e:
        print(f"Error during conversion: {e}")
        return False


def sanitize_filename(song_name, author_name):
    combined_name = f"{song_name} - {author_name}" if author_name else song_name
    invalid_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
    for char in invalid_chars:
        combined_name = combined_name.replace(char, '_')
    return combined_name.strip().rstrip('.')


# Define the GitHub upload function
def github_upload_function(filename, filepath):
    with open(filepath, 'rb') as f:
        file_content = f.read()
    encoded_content = base64.b64encode(file_content).decode()
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/downloads/{filename}"
    payload = {
        "message": f"Add {filename}",
        "content": encoded_content
    }
    headers = {
        "Authorization": f"Bearer {MY_GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    response = requests.put(url, headers=headers, data=json.dumps(payload))
    if response.status_code == 201:
        return response.json().get("content", {}).get("download_url")
    return None


def fetch_spotify_data(song_url):
    api_url = "https://spotify-downloader9.p.rapidapi.com/downloadSong"
    querystring = {"songId": song_url}
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": "spotify-downloader9.p.rapidapi.com"
    }
    response = requests.get(api_url, headers=headers, params=querystring).json()
    return response

def upload_to_github(filename, filepath):
    return github_upload_function(filename, filepath)

@songRoutes.route('/song-info-to-audio', methods=['POST'])
def song_info_to_audio():
    start_time = time.time()  # Start time for the entire request

    try:
        data = request.json
        song_name = data.get('songName')
        author_name = data.get('authorName')
        spotify_song_url = data.get('spotifyUrl')

        if not all([song_name, author_name, spotify_song_url]):
            return jsonify({'success': False, 'error': 'songName, authorName, spotifyUrl are required'}), 400

        sanitized_song_name = sanitize_filename(song_name, author_name)
        filename = f"{sanitized_song_name}.mp3"
        filepath = os.path.join(DOWNLOAD_FOLDER, filename)

        # Check if file exists on GitHub
        github_headers = {"Authorization": f"Bearer {MY_GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        github_check_start = time.time()  # Time for GitHub check
        response = requests.get(f'https://api.github.com/repos/{GITHUB_REPO}/contents/downloads/{filename}', headers=github_headers)
        print(f"GitHub check time: {time.time() - github_check_start} seconds")

        if response.status_code == 200:
            file_info = response.json()
            print(f"Total time till GitHub check: {time.time() - start_time} seconds")
            return jsonify({'success': True, 'audio_url': file_info['download_url'], 'song_name': song_name})

        # Fetch song from Spotify (or external service)
        spotify_fetch_start = time.time()  # Time for Spotify fetch
        fetch_response = fetch_spotify_data(spotify_song_url)
        print(f"Spotify fetch time: {time.time() - spotify_fetch_start} seconds")

        if fetch_response.get('success'): 
            download_link = fetch_response['data'].get('downloadLink')

            if download_link:
                audio_response = requests.get(download_link, stream=True)
                with open(filepath, 'wb') as f:
                    f.write(audio_response.content)

                # Upload to GitHub
                upload_start = time.time()  # Time for GitHub upload
                upload_url = upload_to_github(filename, filepath)
                print(f"GitHub upload time: {time.time() - upload_start} seconds")

                os.remove(filepath)  # Cleanup local file
                print(f"Total time: {time.time() - start_time} seconds")
                if upload_url:
                    return jsonify({'success': True, 'audio_url': upload_url, 'song_name': song_name})
                else:
                    return jsonify({'success': False, 'error': 'Error uploading file to GitHub'}), 500
            else:
                return jsonify({'success': False, 'error': 'No download link found in response'}), 500
        else:
            return jsonify({'success': False, 'error': 'Failed to fetch song from Spotify API'}), 500

    except Exception as e:
        print(f"Error in song_info_to_audio route: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    songRoutes.run(debug=True, port=2007)
 