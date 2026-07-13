# -*- coding: utf-8 -*-

import json
import re
import time
import urllib.parse
import urllib.request

from puddlestuff.audioinfo import DATA, get_mime
from puddlestuff.constants import CHECKBOX
from puddlestuff.tagsources import RetrievalError, get_useragent, parse_searchstring, write_log
from puddlestuff.util import translate

API_BASE = 'https://api.discogs.com'
SEARCH_URL = API_BASE + '/database/search'
RELEASE_URL = API_BASE + '/releases/{}'
REQUEST_INTERVAL = 1.0
DEFAULT_LIMIT = 10

TITLE_CLEAN_RE = [
    (re.compile(r'\(.*?\)'), ''),
    (re.compile(r'\[.*?\]'), ''),
    (re.compile(r'\b(feat|ft|featuring)\b\.?.*', re.I), ''),
    (re.compile(r'\b(remaster(?:ed)?|mono|stereo|live|demo|edit|version|mix)\b.*', re.I), ''),
]
YEAR_RE = re.compile(r'^\d{4}')


def clean_text(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def clean_title(title):
    title = title or ''
    for pattern, replacement in TITLE_CLEAN_RE:
        title = pattern.sub(replacement, title)
    return clean_text(title)


def first_year(value):
    matched = YEAR_RE.search(value or '')
    if matched:
        return matched.group(0)


def normalize_name(value):
    value = clean_text(value)
    value = re.sub(r'\s+\(\d+\)$', '', value)
    return value


def parse_release_title(value):
    value = clean_text(value)
    if ' - ' in value:
        artist, album = value.split(' - ', 1)
        return normalize_name(artist), album.strip()
    return '', value


def compare_text(expected, actual):
    expected = clean_text(expected).lower()
    actual = clean_text(actual).lower()
    if not expected or not actual:
        return 0
    if expected == actual:
        return 25
    if expected in actual or actual in expected:
        return 12
    expected_words = set(expected.split())
    actual_words = set(actual.split())
    if expected_words and actual_words:
        overlap = len(expected_words & actual_words)
        if overlap:
            return min(10, overlap * 3)
    return 0


def track_position_key(position):
    numbers = re.findall(r'\d+', position or '')
    if not numbers:
        return (0, 0, position or '')
    if len(numbers) == 1:
        return (0, int(numbers[0]), position or '')
    return (int(numbers[0]), int(numbers[1]), position or '')


class DiscogsSong(object):
    name = 'Discogs Song'
    group_by = ['artist', None]
    tooltip = translate(
        'Discogs Song',
        """<p>Searches Discogs releases using artist and song title.</p>
        <ul>
        <li>Use <b>artist;title</b> for a targeted song lookup.</li>
        <li>Any other text is sent as a raw Discogs release search.</li>
        <li>Results are ranked locally toward the closest artist, title, and album match.</li>
        </ul>"""
    )

    def __init__(self):
        super(DiscogsSong, self).__init__()
        self._last_request_time = 0
        self._get_cover = True
        self.preferences = [
            [translate('Discogs Song', 'Retrieve Cover'), CHECKBOX, True],
        ]

    def applyPrefs(self, args):
        self._get_cover = bool(args[0])

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)

    def _request_json(self, url, params=None):
        if params:
            url = url + '?' + urllib.parse.urlencode(params)

        self._throttle()
        request = urllib.request.Request(url)
        request.add_header('User-Agent', get_useragent())
        request.add_header('Accept', 'application/json')
        write_log(translate('Discogs Song', "Discogs request: {}").format(url))

        try:
            payload = urllib.request.urlopen(request).read()
        except Exception as exc:
            raise RetrievalError(str(exc))
        finally:
            self._last_request_time = time.time()

        try:
            return json.loads(payload)
        except ValueError as exc:
            raise RetrievalError(str(exc))

    def _request_binary(self, url):
        self._throttle()
        request = urllib.request.Request(url)
        request.add_header('User-Agent', get_useragent())

        try:
            payload = urllib.request.urlopen(request).read()
        except Exception as exc:
            raise RetrievalError(str(exc))
        finally:
            self._last_request_time = time.time()

        return payload

    def _search(self, query, title=None, artist=None, album=None):
        params = {
            'type': 'release',
            'q': query,
            'per_page': DEFAULT_LIMIT,
            'page': 1,
        }
        payload = self._request_json(SEARCH_URL, params)
        return self._parse_search_results(payload, title=title, artist=artist, album=album)

    def _parse_search_results(self, payload, title=None, artist=None, album=None):
        results = []
        seen = set()
        requested_title = clean_title(title)
        requested_artist = normalize_name(artist or '')
        requested_album = clean_text(album)

        for item in payload.get('results', []):
            if item.get('type') != 'release':
                continue

            release_id = item.get('id')
            if not release_id:
                continue

            release_artist, release_album = parse_release_title(item.get('title', ''))
            info = {
                'artist': release_artist or requested_artist,
                'album': release_album,
                'title': requested_title or clean_text(title or ''),
                'year': str(item.get('year') or '') or first_year(item.get('released', '')) or '',
                '#discogs_release_id': str(release_id),
                '#discogs_cover_url': item.get('cover_image') or item.get('thumb') or '',
                '#discogs_thumb_url': item.get('thumb') or '',
                '#discogs_resource_url': item.get('resource_url') or '',
            }

            if not info['album']:
                continue

            score = 0
            score += compare_text(requested_artist, info['artist'])
            score += compare_text(requested_album, info['album'])
            score += compare_text(requested_title, item.get('title', ''))
            if info['year']:
                score += 2
            if info['#discogs_cover_url']:
                score += 2

            key = (info['#discogs_release_id'], info['title'], info['album'])
            if key in seen:
                continue
            seen.add(key)

            info['#score'] = str(score)
            results.append((info, []))

        results.sort(key=lambda item: int(item[0].get('#score', '0')), reverse=True)
        return results

    def keyword_search(self, text):
        try:
            params = parse_searchstring(text)
        except RetrievalError:
            params = []

        if params:
            artist, title = params[0]
            query = ' '.join(part for part in [artist, clean_title(title)] if part)
            return self._search(query, title=title, artist=artist)

        return self._search(clean_text(text), title=clean_text(text))

    def search(self, artist, files=None):
        results = []
        for modeltag in files or []:
            title_values = modeltag.get('title') or ['']
            album_values = modeltag.get('album') or ['']
            title = clean_title(title_values[0])
            album = clean_text(album_values[0])
            if not artist or not title:
                continue

            queries = [
                ' '.join(part for part in [artist, title, album] if part),
                ' '.join(part for part in [artist, title] if part),
                ' '.join(part for part in [artist, album] if part),
            ]

            found = []
            for query in queries:
                if not query:
                    continue
                found = self._search(query, title=title, artist=artist, album=album)
                if found:
                    break
            results.extend(found)

        deduped = []
        seen = set()
        for item in results:
            key = (
                item[0].get('#discogs_release_id'),
                item[0].get('artist'),
                item[0].get('album'),
                item[0].get('title'),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _fetch_cover(self, albuminfo, release):
        if not self._get_cover:
            return []

        candidates = []
        if albuminfo.get('#discogs_cover_url'):
            candidates.append(albuminfo.get('#discogs_cover_url'))
        for image in release.get('images', []):
            uri = image.get('uri')
            if uri and uri not in candidates:
                candidates.append(uri)

        for url in candidates:
            try:
                payload = self._request_binary(url)
            except RetrievalError as exc:
                write_log(str(exc))
                continue
            return [{DATA: payload, 'mime': get_mime(payload)}]
        return []

    def retrieve(self, albuminfo):
        release_id = albuminfo.get('#discogs_release_id')
        if not release_id:
            raise RetrievalError(translate('Discogs Song', 'No Discogs release id available.'))

        release = self._request_json(RELEASE_URL.format(release_id))
        artist_names = [normalize_name(artist.get('name', '')) for artist in release.get('artists', [])]
        artist = ' & '.join([name for name in artist_names if name])
        year = first_year(release.get('released', '')) or str(release.get('year') or '') or albuminfo.get('year', '')

        info = albuminfo.copy()
        info.update({
            'artist': artist or albuminfo.get('artist', ''),
            'album': release.get('title') or albuminfo.get('album', ''),
            'year': year,
            '#discogs_resource_url': release.get('resource_url') or albuminfo.get('#discogs_resource_url', ''),
        })

        images = self._fetch_cover(albuminfo, release)
        if images:
            info['__image'] = images

        tracks = []
        requested_title = clean_title(albuminfo.get('title', ''))
        for track in release.get('tracklist', []):
            position = clean_text(track.get('position', ''))
            title = clean_text(track.get('title', ''))
            duration = clean_text(track.get('duration', ''))
            if not title:
                continue

            artists = [normalize_name(entry.get('name', '')) for entry in track.get('artists', [])]
            track_artist = ' & '.join([name for name in artists if name]) or info['artist']
            track_info = {
                'artist': track_artist,
                'album': info['album'],
                'title': title,
                'track': position,
                'year': year,
            }
            if duration:
                track_info['__length'] = duration
            if requested_title and clean_title(title) == requested_title:
                track_info['title'] = albuminfo.get('title', title)
            tracks.append(track_info)

        tracks.sort(key=lambda item: track_position_key(item.get('track', '')))
        return info, tracks


tagsources = [DiscogsSong]
info = DiscogsSong
