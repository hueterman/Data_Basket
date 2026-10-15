"""Partidos cuya fuente no publica estadisticas: se cuentan aparte, no como fallo."""
import pytest
from bs4 import BeautifulSoup

from src.pipeline.ingest import ingest_source
from src.scraper import feb as feb_mod
from src.scraper.base import BoxscoreData, BoxscoreNotPublished, GameRef, PlayerStatLine, Team, TeamStatLine

PANEL_SIN_TABLAS = """
<div id="EstaticoPanel">
  <div class="columna equipo local"><a href="Equipo.aspx?i=1">Local</a></div>
  <div class="columna equipo visitante"><a href="Equipo.aspx?i=2">Visitante</a></div>
</div>"""


def test_feb_ficha_sin_tablas_lanza_not_published(monkeypatch):
    src = feb_mod.FebSource("feb", "2020")
    monkeypatch.setattr(src, "_get_soup",
                        lambda url, context: BeautifulSoup(PANEL_SIN_TABLAS, "html.parser"))
    with pytest.raises(BoxscoreNotPublished) as exc:
        src.get_boxscore("2207069")
    assert isinstance(exc.value, feb_mod.FebSourceError)      # sigue siendo un FebSourceError


class _Fake:
    source = "feb"

    def list_teams(self, season):
        return [Team("1", "Local", "feb"), Team("2", "Visitante", "feb")]

    def list_players(self, team_id):
        return []

    def list_games(self, season):
        return [GameRef(g, "feb", season, 1, "2021-01-09", "1", "2", 70, 60, True)
                for g in ("g_ok", "g_sin", "g_roto")]

    def get_boxscore(self, game_id):
        if game_id == "g_sin":
            raise BoxscoreNotPublished("sin estadisticas")
        if game_id == "g_roto":
            raise RuntimeError("fallo real")
        return BoxscoreData(
            game_id=game_id,
            team_stats=[TeamStatLine(game_id, "1", 1, 2, 1, 2, 1, 2, 1, 1, 1, 1, 1, 1, 1, 1)],
            player_stats=[PlayerStatLine(game_id, "p1", "P Uno", "1", 10.0, 5, 1, 1, 3, 0)],
        )

    def get_shots(self, game_id):
        raise RuntimeError("sin tiros")


def test_ingest_cuenta_sin_estadisticas_aparte_de_fallos(tmp_path):
    from src.pipeline.db import get_connection
    conn = get_connection(tmp_path / "t.db")
    msgs = []
    s = ingest_source(_Fake(), "2020", conn=conn, raw_dir=tmp_path / "raw",
                      use_cache=False, progress=msgs.append)
    assert (s.n_boxscores_ok, s.n_boxscores_failed, s.n_boxscores_no_stats) == (1, 1, 1)
    assert s.no_stats_game_ids == ["g_sin"] and s.failed_game_ids == ["g_roto"]
    assert "1 sin estadisticas en la fuente" in str(s)
    # solo el fallo real genera aviso por partido
    assert sum("aviso: boxscore" in m for m in msgs) == 1
