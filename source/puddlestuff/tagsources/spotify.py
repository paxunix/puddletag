# -*- coding: utf-8 -*-

import puddlestuff
from puddlestuff.audioinfo import (DATA, get_mime)
from puddlestuff.constants import CHECKBOX, COMBO, TEXT
from puddlestuff.tagsources import (write_log, RetrievalError, urlopen, parse_searchstring)
from puddlestuff.util import translate
import re
import spotipy
import spotipy.oauth2 as oauth2


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

        credentials = oauth2.SpotifyClientCredentials(client_id=self.spotifyClientKey, client_secret=self.spotifyClientSecret)

        self.spotify = spotipy.Spotify(auth = credentials.get_access_token())


    def _spotifySearch(self, query, queryType):
        write_log(translate('Spotify',
            u"Spotify request: query='{}' queryType='{}'".format(query,
                queryType)))

        response = self.spotify.search(q = query, type = queryType, limit = 50)

        return response


    @staticmethod
    def _parseTrack(raw):
        track = {
            "title": raw["name"],
            "track": raw["track_number"],
        }

        if "album" in raw:
            album = Spotify._parseAlbum(raw["album"])
            track["albuminfo"] = album

        return track


    @staticmethod
    def _parseTracks(trackList):
        tracks = []

        for track in trackList:
            tracks.append(Spotify._parseTrack(track))

        # XXX: if the album is multidisc, keep the tracks ordered by disc
        tracks.sort(key = lambda el: el["track"])

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
        album = {
            "artist": raw["artists"][0]["name"],
            "album": raw["name"],
            "year": re.split(r'\D', raw["release_date"])[0],
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
        albumKey2Tracks = VivifyDict();

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


    def keyword_search(self, text):
        #XXX: The format artist1;album1|artist2;album2 should be accepted.
        #Use the parse_searchstring method to separate text
        #into a list of artist album pairs as in:
        # [(artist1, album1), (artist2, album2)]
        # then loop over them
        """Searches for albums/artists/tracks by keyword text."""
        response = self._spotifySearch(text, "album,artist,track")

        # Do not keep track results from possible matches--it only matched
        # on track name and so the album data in the track object won't have
        # the full track list (we can rely on retrieve() to do that work if
        # the user invokes it).
        results = Spotify._parseSpotifySearchResponse(response, keepTracks = False)
        return results


    def search(self, album, artists):
        artist = ""
        if type(artists) is list:
            artist = artists[0]         # XXX: what if more than one artist?
        elif type(artists) is str:
            artist = artists
        else:
            artist = artists.keys()[0]      # XXX: what if more than one artist?

        queryType = ""
        query = ""
        if album and (not artist):
            query = "album:" + album
            queryType = "album"
        elif artist and (not album):
            query = "artist:" + artist
            queryType = "album"
        elif artist and album:
            query = "album:" + album + " " + "artist:" + artist
            queryType = "album"

        response = self._spotifySearch(query, queryType)
        results = Spotify._parseSpotifySearchResponse(response, keepTracks =
                True)

        # If we had artist and album but found no results, it's possible the
        # album name is wrong, so try a search by artist only.
        if len(results) == 0:
            query = "artist:" + artist
            queryType = "album"

            response = self._spotifySearch(query, queryType)
            results = Spotify._parseSpotifySearchResponse(response, keepTracks =
                    True)

        return results


    def retrieve(self, info):
        """ Retrieves track info (and album art) from album+artist in info.  """
        response = self.spotify.album_tracks(info["#spotifyalbumuri"])
        tracks = Spotify._parseTracks(response["items"])
        info["__image"] = Spotify._getImage(info["#spotifyimgurl"])

        return (info, tracks)


tagsources = [Spotify]
info = Spotify

if __name__ == '__main__':
    s = Spotify()
    results = s.search("use your illusion", "")
    print results
