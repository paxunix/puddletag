# -*- coding: utf-8 -*-

import locale
import functools
import puddlestuff
from puddlestuff.audioinfo import (DATA, get_mime)
from puddlestuff.constants import CHECKBOX, COMBO, TEXT
from puddlestuff.tagsources import (write_log, RetrievalError, urlopen, parse_searchstring)
from puddlestuff.util import translate
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os


class VivifyDict(dict):
    def __getitem__(self, item):
        try:
            return dict.__getitem__(self, item)
        except KeyError:
            value = self[item] = type(self)()
            return value


class Spotify(object):
    name = 'Spotify'
    group_by = ['album', 'artist']
    tooltip = translate('Spotify',
        """<p>Enter search parameters here. If empty, the selected files
        are used.</p>
        <ul>
        <li><b>artist;album</b>
        searches for a specific album/artist combination.</li>
        <li>To list the albums by an artist leave off the album part,
        but keep the semicolon (eg. <b>Ratatat;</b>).
        For an album only, leave the artist part as in
        <b>;Resurrection.</li>
        <li>Entering keywords <b>without a semi-colon (;)</b> will do a
        keyword search across albums, artists, and tracks.</li>
        </ul>""")


    def __init__(self):
        super(Spotify, self).__init__()

        self.preferences = [
            [translate('Spotify', 'Client Key'), TEXT, ''],
            [translate('Spotify', 'Client Secret (Stored as plain-text.)'), TEXT, '']
        ]


    @staticmethod
    def _getToken(key, secret):
        return credentials.get_access_token()


    def applyPrefs(self, args):
        self.spotifyClientKey = args[0]
        self.spotifyClientSecret = args[1]

        self.spotifyAuthManager = SpotifyClientCredentials(client_id=self.spotifyClientKey, client_secret=self.spotifyClientSecret)

        self.spotify = spotipy.Spotify(auth_manager = self.spotifyAuthManager)


    def _spotifySearch(self, queryData):
        write_log(translate('Spotify',
            u"Spotify request: query='{}' queryType='{}'".format(queryData["query"], queryData["queryType"])))

        response = self.spotify.search(q = queryData["query"],
            type = queryData["queryType"], limit = 50)

        return response


    @staticmethod
    def _parseTrack(raw):
        track = {
            "title": raw["name"],
            "track": str(raw["track_number"]),
            "discnumber": str(raw["disc_number"]),
        }

        if "album" in raw:
            album = Spotify._parseAlbum(raw["album"])
            track["albuminfo"] = album

        return track


    @staticmethod
    def _trackCmp(trackObj_a, trackObj_b):
        # sort by disc, then by track number (so we can order multidisc album tracks)
        aDiscNum = str(trackObj_a.get("disc_number", 1)).zfill(5)
        bDiscNum = str(trackObj_b.get("disc_number", 1)).zfill(5)
        aTrackNum = str(trackObj_a.get("track_number", 0)).zfill(5)
        bTrackNum = str(trackObj_b.get("track_number", 0)).zfill(5)

        return locale.strcoll(aDiscNum + aTrackNum, bDiscNum + bTrackNum)


    @staticmethod
    def _parseTracks(trackList):
        tracks = []

        for track in trackList:
            tracks.append(Spotify._parseTrack(track))

        tracks.sort(key = functools.cmp_to_key(Spotify._trackCmp))

        return tracks


    @staticmethod
    def _getImage(url):
        imgdata = urlopen(url)
        return [{
                DATA: imgdata,
                "mime": get_mime(imgdata)
            }]


    @staticmethod
    def _parseAlbum(raw):
        year = "unknown"
        matched = re.search(r'^\d\d\d\d', raw["release_date"])
        if matched:
            year = matched.group(0)

        album = {
            "artist": raw["artists"][0]["name"],
            "album": raw["name"],
            "year": year,
            "#spotifyalbumuri": raw["uri"],
        }

        if "images" in raw and len(raw["images"]) > 0:
            album["#spotifyimgurl"] = raw["images"][0]["url"]

        return album


    @staticmethod
    def _parseAlbums(albumList):
        albums = []

        for album in albumList:
            albums.append(Spotify._parseAlbum(album))

        albums.sort(key = lambda el: el["album"])

        return albums


    @staticmethod
    def _parseSpotifySearchResponse(response, keepTracks):
        albumKey2Tracks = VivifyDict()

        if "albums" in response:
            for album in Spotify._parseAlbums(response["albums"]["items"]):
                albumKey = album["#spotifyalbumuri"]
                albumKey2Tracks[albumKey].setdefault("albuminfo", album)
                albumKey2Tracks[albumKey].setdefault("trackinfo", [])

        # We ignore any artist matches, since there is no album/track info
        # in those objects.

        # Have to loop over tracks even if we're not keeping track data in
        # case there are albums in there that we haven't seen yet.
        if "tracks" in response:
            for track in Spotify._parseTracks(response["tracks"]["items"]):
                if "albuminfo" in track:
                    albumKey = track["albuminfo"]["#spotifyalbumuri"]

                    albumKey2Tracks[albumKey].setdefault("albuminfo", track["albuminfo"])
                    albumKey2Tracks[albumKey].setdefault("trackinfo", [])

                if keepTracks:
                    del track["albuminfo"]      # no longer needed
                    albumKey2Tracks[albumKey]["trackinfo"].append(track)


        return map(lambda key: (albumKey2Tracks[key]["albuminfo"], albumKey2Tracks[key]["trackinfo"]), albumKey2Tracks.keys())


    @staticmethod
    def _buildQuery(artist, album=None, track=None):
        queries = []
        queryTypes = []

        if artist:
            queries.append('artist:"' + artist + '"')
            queryTypes.append('artist')

        if album:
            queries.append('album:"' + album + '"')
            queryTypes.append('album')

        if track:
            queries.append('track:"' + track + '"')
            queryTypes.append('track')

        return { "query": " ".join(queries),
                "queryType": ",".join(queryTypes) }


    @staticmethod
    def _getTrackTitleFromModelTag(modeltag):
        # XXX: not sure [0] is always right
        title = modeltag[0].get("title")[0]

        # Remove parentheticals and things that are not likely to contribute
        # to search hits.
        title = re.sub(r'\(.*?\)', "", title)
        title = re.sub(r'\[.*?\]', "", title)
        title = re.sub(r'\b(feat|ft|featuring)\b\.?.*', "", title, flags = re.IGNORECASE)
        title = re.sub(r'^\s+', "", title)
        title = re.sub(r'\s+$', "", title)

        return title


    def keyword_search(self, text):
        """Searches for albums/artists/tracks by keyword text."""

        results = []
        searchPairs = []
        try:
            searchPairs = parse_searchstring(text)
            for pair in searchPairs:
                queryData = Spotify._buildQuery(pair[0], pair[1])

                if queryData:
                    response = self._spotifySearch(queryData)
                    results.extend(Spotify._parseSpotifySearchResponse(response,
                        keepTracks = False))

        except:
            # Bad input format means just do a text search with the input
            response = self._spotifySearch({
                "query": text, "queryType": "album,artist,track" })
            results.extend(Spotify._parseSpotifySearchResponse(response,
                keepTracks = False))

        return results


    def search(self, album, artists):
        results = []

        for artist in artists.keys():
            # All query types should return album data because that's the
            # only thing that's meaningful (it has track data)

            # Always include track title if we have it
            queryData = Spotify._buildQuery(artist, album,
                Spotify._getTrackTitleFromModelTag(artists[artist]))

            if queryData:
                response = self._spotifySearch(queryData)
                results.extend(Spotify._parseSpotifySearchResponse(response,
                    keepTracks = True))

            # If we had artist and album but found no results, it's possible
            # the album name is wrong, so try a search by artist only (if it
            # has a value)
            if len(results) == 0 and artist:
                queryData = Spotify._buildQuery(artist, None)

                response = self._spotifySearch(queryData)
                results.extend(Spotify._parseSpotifySearchResponse(response,
                    keepTracks = True))

            # If still no results and we have track info, search for it.
            # However, we don't get back complete track listings for the
            # album (it only includes tracks that match the input name).
            # Throw that info away, so a full album retrieve() will be done
            # if/when the user selects the album.
            if len(results) == 0:
                if len(artists[artist]) > 0:
                    title = artists[artist][0].get("title")
                    if title:
                        title = title[0]

                    if title:
                        queryData = Spotify._buildQuery(artist, None, title)

                        response = self._spotifySearch(queryData)
                        results.extend(Spotify._parseSpotifySearchResponse(response, keepTracks = False))

                    # If we still have no results, try a dumb keyword search
                    # with the filename split on punctuation.  Maybe we get
                    # lucky.
                    if not results:
                        keywords = " ".join(re.split(r'\W+', os.path.splitext(artists[artist][0].get("__filename"))[0]))
                        results.extend(self.keyword_search(keywords))

        return results


    def retrieve(self, info):
        """ Retrieves track info (and album art) from album+artist in info.  """
        # Invoked when there is incomplete album data for an artist in
        # existing search results and the user selects an artist.
        response = self.spotify.album_tracks(info["#spotifyalbumuri"])
        tracks = Spotify._parseTracks(response["items"])
        info["__image"] = Spotify._getImage(info["#spotifyimgurl"])

        return (info, tracks)


tagsources = [Spotify]
info = Spotify

if __name__ == '__main__':
    s = Spotify()
    results = s.search("use your illusion", "")
    print(results)
