# -*- coding: utf-8 -*-

import json
import re
import time
import urllib.parse

from puddlestuff.constants import CHECKBOX, COMBO
from puddlestuff.tagsources import RetrievalError, parse_searchstring, urlopen, write_log
from puddlestuff.tagsources.musicbrainz import (
    SERVER, LARGE, ORIG, SMALL, retrieve_album, retrieve_cover_links,
    retrieve_covers, retrieve_front_cover, solr_escape,
)
from puddlestuff.util import translate

TITLE_CLEAN_RE = [
    (re.compile(r'\(.*?\)'), ''),
    (re.compile(r'\[.*?\]'), ''),
    (re.compile(r'\b(feat|ft|featuring)\b\.?.*', re.I), ''),
    (re.compile(r'\b(remaster(?:ed)?|mono|stereo|live|demo|edit|version|mix)\b.*', re.I), ''),
]
YEAR_RE = re.compile(r'^\d{4}')


def clean_title(title):
    title = title or ''
    for pattern, replacement in TITLE_CLEAN_RE:
        title = pattern.sub(replacement, title)
    return re.sub(r'\s+', ' ', title).strip()


def first_year(value):
    matched = YEAR_RE.search(value or '')
    if matched:
        return matched.group(0)


def artist_credit_text(credit):
    parts = []
    for item in credit or []:
        name = item.get('name') or item.get('artist', {}).get('name')
        if name:
            parts.append(name)
        joinphrase = item.get('joinphrase') or item.get('join-phrase')
        if joinphrase:
            parts.append(joinphrase)
    return ''.join(parts).strip()


def release_score(release):
    score = 0
    group = release.get('release-group') or {}
    primary = (group.get('primary-type') or '').lower()
    secondary = [value.lower() for value in group.get('secondary-types', [])]
    status = (release.get('status') or '').lower()

    if status == 'official':
        score += 10
    if primary == 'album':
        score += 12
    elif primary == 'ep':
        score += 5
    elif primary == 'single':
        score += 3

    if 'compilation' in secondary:
        score -= 6
    if 'live' in secondary:
        score -= 4
    if 'soundtrack' in secondary:
        score -= 3

    if release.get('date'):
        score += 2
    return score


def recording_query_url(query, limit=10):
    encoded = urllib.parse.urlencode({
        'query': query,
        'fmt': 'json',
        'limit': limit,
    })
    return SERVER + 'recording?' + encoded


def build_field_query(artist, title):
    parts = []
    if artist:
        parts.append('artist:' + solr_escape(artist))
    if title:
        parts.append('recording:' + solr_escape(title))
    return ' AND '.join(parts)


def build_raw_query(text):
    return clean_title(text)


def parse_recording_results(data):
    results = []
    seen = set()
    for recording in data.get('recordings', []):
        recording_artist = artist_credit_text(recording.get('artist-credit'))
        title = recording.get('title', '')
        year = first_year(recording.get('first-release-date', ''))
        base_score = int(recording.get('score', 0))

        for release in recording.get('releases', []):
            album_id = release.get('id')
            album = release.get('title')
            if not album_id or not album:
                continue
            key = (album_id, recording.get('id'))
            if key in seen:
                continue
            seen.add(key)

            info = {
                'artist': recording_artist,
                'album': album,
                'title': title,
                '#album_id': album_id,
                'mbrainz_album_id': album_id,
                '#recording_id': recording.get('id'),
                'mbrainz_recording_id': recording.get('id'),
                '#score': str(base_score + release_score(release)),
            }

            track_count = release.get('track-count')
            if track_count:
                info['__numtracks'] = str(track_count)

            release_year = first_year(release.get('date', ''))
            if year or release_year:
                info['year'] = year or release_year

            results.append((info, []))

    results.sort(key=lambda item: int(item[0].get('#score', '0')), reverse=True)
    return results


class MusicBrainzSong(object):
    name = 'MusicBrainz Song'
    group_by = ['artist', None]
    tooltip = translate('MusicBrainz Song',
        """<p>Searches MusicBrainz recordings using artist and song title.</p>
        <ul>
        <li>Use <b>artist;title</b> for a targeted song lookup.</li>
        <li>Any other text is sent as a raw recording search.</li>
        <li>Results are ranked toward official album releases.</li>
        </ul>""")

    def __init__(self):
        super(MusicBrainzSong, self).__init__()
        self.__lasttime = 0
        self.__image_size = LARGE
        self.__num_images = 0
        self.__get_images = True
        self.preferences = [
            [translate('MusicBrainz Song', 'Retrieve Cover'), CHECKBOX, True],
            [translate('MusicBrainz Song', 'Cover size to retrieve:'), COMBO,
             [[translate('Amazon', 'Small'),
               translate('Amazon', 'Large'),
               translate('Amazon', 'Original Size')], 1]],
            [translate('MusicBrainz Song', 'Amount of images to retrieve:'), COMBO,
             [[translate('MusicBrainz Song', 'Just the front cover'),
               translate('MusicBrainz Song', 'All (can take a while)')], 0]],
        ]

    def applyPrefs(self, args):
        self.__get_images = bool(args[0])
        self.__image_size = args[1]
        self.__num_images = args[2]

    def _throttle(self):
        if time.time() - self.__lasttime < 1:
            time.sleep(1)

    def _search_query(self, query):
        self._throttle()
        url = recording_query_url(query)
        write_log(translate('MusicBrainz Song', "MusicBrainz recording search: {}").format(query))
        payload = json.loads(urlopen(url))
        self.__lasttime = time.time()
        return parse_recording_results(payload)

    def keyword_search(self, text):
        try:
            params = parse_searchstring(text)
        except RetrievalError:
            params = []

        if params:
            artist, title = params[0]
            return self._search_query(build_field_query(artist, clean_title(title)))

        return self._search_query(build_raw_query(text))

    def search(self, artist, files=None):
        results = []
        for modeltag in files or []:
            track_artist = artist
            title = clean_title(modeltag.get('title', [''])[0] if modeltag.get('title') else '')
            if not track_artist or not title:
                continue
            results.extend(self._search_query(build_field_query(track_artist, title)))

        deduped = []
        seen = set()
        for item in results:
            key = item[0].get('#album_id'), item[0].get('#recording_id')
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def retrieve(self, albuminfo):
        try:
            album_id = albuminfo['#album_id']
        except TypeError:
            album_id = albuminfo

        self._throttle()
        ret = retrieve_album(album_id)
        self.__lasttime = time.time()
        image = self.retrieve_covers(album_id)
        if image:
            ret[0]['__image'] = image

        recording_id = albuminfo.get('#recording_id')
        requested_title = clean_title(albuminfo.get('title', ''))
        if recording_id or requested_title:
            for track in ret[1]:
                track_title = clean_title(track.get('title', ''))
                if recording_id and track.get('mbrainz_track_id') == recording_id:
                    track.update({'title': albuminfo.get('title', track.get('title', ''))})
                    break
                if requested_title and track_title == requested_title:
                    break

        return ret

    def retrieve_covers(self, album_id):
        if not self.__get_images:
            return []
        if self.__num_images == 0:
            try:
                image = retrieve_front_cover(album_id)
                return [image]
            except RetrievalError as exc:
                write_log(str(exc))
                return []

        try:
            cover_links = retrieve_cover_links(album_id)
        except RetrievalError as exc:
            write_log(str(exc))
            return []
        return retrieve_covers(cover_links, self.__image_size)


tagsources = [MusicBrainzSong]
info = MusicBrainzSong
