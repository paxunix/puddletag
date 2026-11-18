import pytest

from puddlestuff.audioinfo import DATA
from puddlestuff.tagsources import RetrievalError, youtube


def video(video_id, title, description='', channel='Artist - Topic', year=''):
    return {
        'id': {'videoId': video_id},
        'snippet': {
            'title': title,
            'description': description,
            'channelTitle': channel,
            'channelId': 'channel-' + video_id,
            'publishedAt': (year or '2026') + '-01-01T00:00:00Z',
            'thumbnails': {
                'high': {'url': 'https://img.example/' + video_id + '.jpg'},
            },
        },
    }


def test_query_parts_accepts_optional_artist_title_and_album():
    assert youtube.query_parts('Artist;Title;Album') == ('Artist', 'Title', 'Album')
    assert youtube.query_parts(';Title;') == ('', 'Title', '')
    assert youtube.query_parts('Title') == ('', 'Title', '')


def test_query_parts_rejects_more_than_three_fields():
    with pytest.raises(RetrievalError):
        youtube.query_parts('Artist;Title;Album;Extra')


def test_search_results_rank_exact_song_and_extract_year():
    payload = {
        'items': [
            video('cover', 'Artist - Title (Live Cover)', channel='Someone'),
            video('official', 'Artist - Title (Official Audio)',
                  'Released 2026\nAlbum: New Album', channel='Artist - Topic'),
        ]
    }

    results = youtube.parse_search_results(
        payload, artist='Artist', title='Title', album='New Album')

    assert [item[0]['#youtube_video_id'] for item in results] == ['official', 'cover']
    assert results[0][0]['year'] == '2026'
    assert results[0][0]['#youtube_year_source'] == 'description'
    assert results[0][0]['album'] == 'New Album'
    assert results[0][0]['#youtube_thumbnail_url'].endswith('official.jpg')


def test_search_requires_an_api_key():
    source = youtube.YouTube()
    with pytest.raises(RetrievalError, match='API key'):
        source.keyword_search('Artist;Title;Album')


def test_search_uses_youtube_api_and_hides_api_key_from_log(monkeypatch):
    source = youtube.YouTube()
    source.applyPrefs(['secret-key', False])
    calls = []

    def request(endpoint, params):
        calls.append((endpoint, params))
        return {'items': [video('one', 'Artist - Title')]}

    monkeypatch.setattr(source, '_request_json', request)
    results = source.keyword_search('Artist;Title;Album')

    assert results[0][0]['#youtube_video_id'] == 'one'
    assert calls[0][1] == {
        'part': 'snippet', 'q': 'Artist Title Album', 'type': 'video',
        'maxResults': youtube.DEFAULT_LIMIT, 'order': 'relevance',
    }


def test_retrieve_refreshes_metadata_and_can_retrieve_thumbnail(monkeypatch):
    source = youtube.YouTube()
    source.applyPrefs(['secret-key', True])
    requests = []

    def request(endpoint, params):
        requests.append((endpoint, params))
        return {'items': [video(
            'one', 'Artist - Title', 'Copyright 2025 Artist', year='2025')]}

    monkeypatch.setattr(source, '_request_json', request)
    monkeypatch.setattr(youtube, 'urlopen', lambda url: b'\xff\xd8synthetic jpeg')

    info, tracks = source.retrieve({
        'artist': 'Artist', 'title': 'Title', 'album': '',
        '#youtube_video_id': 'one',
        '#youtube_thumbnail_url': 'https://img.example/old.jpg',
    })

    assert requests[0][0] == youtube.VIDEOS_URL
    assert info['year'] == '2025'
    assert info['#youtube_year_source'] == 'description'
    assert info['__image'][0][DATA] == b'\xff\xd8synthetic jpeg'
    assert info['album'] == 'Title'
    assert tracks == [{'artist': 'Artist', 'album': 'Title', 'title': 'Title', 'year': '2025'}]
