import datetime
from concurrent.futures import ThreadPoolExecutor

import flask

from app import app, private, streams
from app.db import Opening


def _load_game_status(game_stream: streams.GameStream) -> dict:
    live_stream = None
    broadcast = None
    error = None
    try:
        live_stream = game_stream.find_live_stream()
        broadcast = game_stream.sync_broadcast_status()
    except Exception as e:
        error = str(e)
    return {
        "game": game_stream,
        "live_stream": live_stream,
        "broadcast": broadcast,
        "error": error,
    }


@private.get("/streams/")
def streams_page():
    game_streams = streams.get()
    # Each game does its own sequential round of YouTube API calls; running
    # them in threads overlaps that network I/O across games instead of
    # summing it, which is what actually makes this page slow to load.
    with ThreadPoolExecutor(max_workers=len(game_streams) or 1) as executor:
        games = list(executor.map(_load_game_status, game_streams))
    return app.render("streams", games=games, streams_ready=streams.is_ready)


@private.route("/streams/<string:game>/create-live-stream/")
def streams_create_live_stream(game):
    matches = [g for g in streams.get() if g.game == game]
    if not matches:
        flask.abort(404)
    matches[0].create_live_stream()
    flask.flash(f"Live stream créé pour {game}")
    return flask.redirect(flask.url_for(".streams_page"))


def _find_game(game: str) -> streams.GameStream:
    matches = [g for g in streams.get() if g.game == game]
    if not matches:
        flask.abort(404)
    return matches[0]


@private.get("/api/streams/<string:game>/status/")
def api_streams_status(game):
    game_stream = _find_game(game)
    try:
        return streams.StreamStatusResponse(
            broadcast=game_stream.sync_broadcast_status(),
            live_stream=game_stream.find_live_stream(),
        )
    except Exception as e:
        return {"error": str(e)}, 500


@private.post("/api/streams/<string:game>/start/")
def api_streams_start(game):
    game_stream = _find_game(game)
    privacy = (flask.request.get_json(silent=True) or {}).get("privacy")
    try:
        return streams.StreamStatusResponse(
            broadcast=game_stream.start_broadcast(privacy=privacy),
            live_stream=game_stream.find_live_stream(),
        )
    except Exception as e:
        return {"error": str(e)}, 500


@private.post("/api/streams/<string:game>/stop/")
def api_streams_stop(game):
    game_stream = _find_game(game)
    try:
        return streams.StreamStatusResponse(
            broadcast=game_stream.stop_broadcast(),
            live_stream=game_stream.find_live_stream(),
        )
    except Exception as e:
        return {"error": str(e)}, 500


@private.post("/api/streams/start-all/")
def api_streams_start_all():
    privacy_by_game = flask.request.get_json(silent=True) or {}
    results = {}
    for game_stream in streams.get():
        try:
            game_stream.start_broadcast(privacy=privacy_by_game.get(game_stream.game))
            results[game_stream.game] = None
        except Exception as e:
            results[game_stream.game] = str(e)
    return results


@private.post("/api/streams/stop-all/")
def api_streams_stop_all():
    results = {}
    for game_stream in streams.get():
        try:
            game_stream.stop_broadcast()
            results[game_stream.game] = None
        except Exception as e:
            results[game_stream.game] = str(e)
    return results


@private.post("/api/streams/check-auto-start/")
def api_streams_check_auto_start():
    """Start all streams for openings that just began and asked for it.

    Polled from the Streams page (see refreshAll in streams.html.j2) rather
    than from a cron job - only fires while someone has that page open, but
    needs no extra server-side scheduling infrastructure.
    """
    now = datetime.datetime.now()
    with app.session() as s:
        pending = (
            s.query(Opening)
            .filter(
                Opening.auto_start_streams.is_(True),
                Opening.streams_started_at.is_(None),
                Opening.start <= now,
                Opening.end >= now,
            )
            .all()
        )

        results = {}
        for opening in pending:
            for game_stream in streams.get():
                try:
                    game_stream.start_broadcast()
                    results[game_stream.game] = None
                except Exception as e:
                    results[game_stream.game] = str(e)
            opening.streams_started_at = now

        s.commit()

    return results
