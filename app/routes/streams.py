import flask

from app import app, private, streams


@private.get("/streams/")
def streams_page():
    games = []
    for game_stream in streams.get():
        live_stream = None
        broadcast = None
        error = None
        try:
            live_stream = game_stream.find_live_stream()
            broadcast = game_stream.sync_broadcast_status()
        except Exception as e:
            error = str(e)
        games.append(
            {
                "game": game_stream,
                "live_stream": live_stream,
                "broadcast": broadcast,
                "error": error,
            }
        )
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
