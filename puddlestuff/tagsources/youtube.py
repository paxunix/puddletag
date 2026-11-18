# -*- coding: utf-8 -*-

"""YouTube Data API tag source for newly published songs."""

import json
import re
import time
import urllib.parse

from puddlestuff.audioinfo import DATA, get_mime
from puddlestuff.constants import CHECKBOX, TEXT
from puddlestuff.tagsources import RetrievalError, urlopen, write_log
from puddlestuff.util import translate, to_string

API_BASE = 'https://www.googleapis.com/youtube/v3'
SEARCH_URL = API_BASE + '/search'
VIDEOS_URL = API_BASE + '/videos'
VIDEO_URL = 'https://www.youtube.com/watch?v={}'
REQUEST_INTERVAL = 0.1
DEFAULT_LIMIT = 10

YEAR_RE = re.compile(r'(?<!\d)(19\d{2}|20\d{2})(?!\d)')
TITLE_SEPARATOR_RE = re.compile(r'\s+[-–—|]\s+')
NOISE_RE = re.compile(
    r'\b(?:official\s+(?:audio|video|music\s+video)|official|lyrics?|'
    r'visualizer|video|audio|hd|4k)\b', re.I)
NEGATIVE_RE = re.compile(
    r'\b(?:karaoke|cover|reaction|instrumental|nightcore|sped\s*up|'
    r'slowed|slowed\s*\+\s*reverb|remix|live|acoustic)\b', re.I)
ALBUM_RE = re.compile(r'(?:album|from the album|off the album)\s*[:\-]\s*([^\n|]+)', re.I)


def clean_text(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def clean_title(value):
    value = clean_text(value)
    value = re.sub(r'\([^)]*\)|\[[^]]*\]', '', value)
    value = NOISE_RE.sub('', value)
    return clean_text(value).strip('-–—| ')


def first_year(*values):
    for value in values:
        match = YEAR_RE.search(value or '')
        if match:
            return match.group(1)


def first_album(value):
    match = ALBUM_RE.search(value or '')
    if match:
        return clean_text(match.group(1)).strip(' .')


def compare_text(expected, actual):
    expected = clean_title(expected).casefold()
    actual = clean_title(actual).casefold()
    if not expected or not actual:
        return 0
    if expected == actual:
        return 30
    if expected in actual or actual in expected:
        return 15
    expected_words = set(expected.split())
    actual_words = set(actual.split())
    return min(10, len(expected_words & actual_words) * 3)


def split_title(value):
    parts = TITLE_SEPARATOR_RE.split(clean_text(value), maxsplit=1)
    if len(parts) == 2:
        return clean_text(parts[0]), clean_title(parts[1])
    return '', clean_title(value)


def video_id(video):
    value = video.get('id') or ''
    return value.get('videoId', '') if isinstance(value, dict) else value


def query_parts(text):
    """Return artist, title, album from YouTube's three-part search syntax."""
    text = clean_text(text)
    if not text or ';' not in text:
        return '', clean_title(text), ''
    parts = [clean_text(part) for part in text.split(';')]
    if len(parts) > 3:
        raise RetrievalError(translate(
            'YouTube', '<b>Use artist;title;album for YouTube searches.</b>'))
    parts += [''] * (3 - len(parts))
    return parts[0], clean_title(parts[1]), clean_text(parts[2])


def build_queries(artist='', title='', album=''):
    values = []
    for parts in ((artist, title, album), (artist, title),
                  (artist, album), (title, album), (title,)):
        query = clean_text(' '.join(part for part in parts if part))
        if query and query not in values:
            values.append(query)
    return values


def extract_metadata(video, artist='', title='', album=''):
    snippet = video.get('snippet') or {}
    raw_title = clean_text(snippet.get('title', ''))
    description = clean_text(snippet.get('description', ''))
    parsed_artist, parsed_title = split_title(raw_title)
    description_year = first_year(description)
    title_year = first_year(raw_title)
    upload_year = first_year(snippet.get('publishedAt', ''))
    year = description_year or title_year or upload_year or ''
    year_source = ('description' if description_year else
                   'title' if title_year else
                   'upload' if upload_year else '')
    album_value = (clean_text(album) or first_album(description) or
                   clean_title(title) or parsed_title)
    return {
        'artist': clean_text(artist) or parsed_artist or clean_text(snippet.get('channelTitle', '')),
        'title': clean_title(title) or parsed_title,
        'album': album_value,
        'year': year or '',
        '#youtube_year_source': year_source,
        '#youtube_channel_id': snippet.get('channelId', ''),
        '#youtube_video_id': video_id(video),
        '#youtube_thumbnail_url': ((snippet.get('thumbnails') or {}).get('maxres') or
                                   (snippet.get('thumbnails') or {}).get('high') or
                                   (snippet.get('thumbnails') or {}).get('default') or {}).get('url', ''),
        '#youtube_url': VIDEO_URL.format(video_id(video)),
    }


def score_candidate(info, raw_title, description, artist='', title='', album='', channel=''):
    score = compare_text(title, info['title'])
    score += compare_text(artist, info['artist'])
    score += compare_text(album, info['album'])
    score += compare_text(title, raw_title)
    score += compare_text(artist, raw_title)
    if channel and (channel.casefold().endswith(' - topic') or
                    'official' in channel.casefold()):
        score += 8
    if first_year(description, raw_title):
        score += 2
    score -= len(NEGATIVE_RE.findall(raw_title + ' ' + description)) * 12
    return score


def parse_search_results(payload, artist='', title='', album=''):
    results = []
    seen = set()
    for item in payload.get('items', []):
        video_id = (item.get('id') or {}).get('videoId')
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        snippet = item.get('snippet') or {}
        info = extract_metadata(item, artist, title, album)
        info['#score'] = str(score_candidate(
            info, snippet.get('title', ''), snippet.get('description', ''),
            artist, title, album, snippet.get('channelTitle', '')))
        info['#extrainfo'] = (translate('YouTube', 'YouTube video'),
                              info['#youtube_url'])
        results.append((info, []))
    results.sort(key=lambda item: int(item[0].get('#score', 0)), reverse=True)
    return results


class YouTube:
    name = 'YouTube'
    group_by = ['artist', None]
    tooltip = translate(
        'YouTube',
        """<p>Searches YouTube videos using artist, title, and album.</p>
        <ul>
        <li>Use <b>artist;title;album</b>; empty fields are allowed.</li>
        <li>Any text without semicolons is used as a title search.</li>
        <li>Year and album data may be inferred from video metadata.</li>
        </ul>"""
    )

    def __init__(self):
        self._api_key = ''
        self._get_thumbnail = True
        self._last_request = 0
        self.preferences = [
            [translate('YouTube', 'API key (stored as plain text)'), TEXT, ''],
            [translate('YouTube', 'Retrieve video thumbnail'), CHECKBOX, True],
        ]

    def applyPrefs(self, args):
        self._api_key = (args[0] or '').strip()
        self._get_thumbnail = bool(args[1])

    def _request_json(self, endpoint, params):
        if not self._api_key:
            raise RetrievalError(translate(
                'YouTube', 'A YouTube Data API key is required.'))
        elapsed = time.monotonic() - self._last_request
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        params = dict(params)
        params['key'] = self._api_key
        url = endpoint + '?' + urllib.parse.urlencode(params)
        safe_url = endpoint + '?' + urllib.parse.urlencode({
            key: value for key, value in params.items() if key != 'key'})
        write_log(translate('YouTube', 'YouTube request: {}').format(safe_url))
        try:
            payload = json.loads(urlopen(url))
        except (TypeError, ValueError) as exc:
            raise RetrievalError(str(exc))
        finally:
            self._last_request = time.monotonic()
        if payload.get('error'):
            error = payload['error'].get('message', 'YouTube request failed.')
            raise RetrievalError(error, payload['error'].get('code', 0))
        return payload

    def _search(self, artist='', title='', album=''):
        queries = build_queries(artist, title, album)
        if not queries:
            return []
        payload = self._request_json(SEARCH_URL, {
            'part': 'snippet',
            'q': queries[0],
            'type': 'video',
            'maxResults': DEFAULT_LIMIT,
            'order': 'relevance',
        })
        return parse_search_results(payload, artist, title, album)

    def keyword_search(self, text):
        artist, title, album = query_parts(text)
        return self._search(artist, title, album)

    def search(self, artist, files=None):
        results = []
        for modeltag in files or []:
            title_values = modeltag.get('title') or ['']
            album_values = modeltag.get('album') or ['']
            title = to_string(title_values[0])
            album = to_string(album_values[0])
            if title:
                results.extend(self._search(artist, title, album))
        deduped = []
        seen = set()
        for item in results:
            video_id = item[0].get('#youtube_video_id')
            if video_id not in seen:
                seen.add(video_id)
                deduped.append(item)
        return deduped

    def _retrieve_video(self, video_id):
        payload = self._request_json(VIDEOS_URL, {
            'part': 'snippet,contentDetails',
            'id': video_id,
        })
        items = payload.get('items', [])
        if not items:
            raise RetrievalError(translate('YouTube', 'Video is unavailable.'))
        return items[0]

    def _retrieve_thumbnail(self, url):
        if not self._get_thumbnail or not url:
            return []
        try:
            payload = urlopen(url)
        except RetrievalError as exc:
            write_log(str(exc))
            return []
        return [{DATA: payload, 'mime': get_mime(payload)}]

    def retrieve(self, video_info):
        video_id = video_info.get('#youtube_video_id')
        if not video_id:
            raise RetrievalError(translate('YouTube', 'No YouTube video id available.'))
        video = self._retrieve_video(video_id)
        info = video_info.copy()
        refreshed = extract_metadata(video)
        for field in ('artist', 'title', 'album', 'year', '#youtube_year_source',
                      '#youtube_channel_id', '#youtube_thumbnail_url'):
            if refreshed.get(field):
                info[field] = refreshed[field]
        info['#youtube_url'] = VIDEO_URL.format(video_id)
        images = self._retrieve_thumbnail(info.get('#youtube_thumbnail_url', ''))
        if images:
            info['__image'] = images
        track = {
            key: info[key] for key in ('artist', 'album', 'title', 'year')
            if info.get(key)
        }
        return info, [track]


tagsources = [YouTube]
info = YouTube
