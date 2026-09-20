"""Funciones de dibujo de media pista, compartidas entre el notebook de
EDA (`notebooks/01_eda_features.ipynb`, secciones 4 y 6) y la app Flask
de exploracion de datos (`app/app.py`).

Extraidas literalmente del notebook -- no reescritas -- porque ya estan
validadas contra datos reales (el patron de tiro NBA/Euroliga/EuroCup
dibujado con estas funciones coincide con el patron tactico moderno
conocido, ver decisiones_proyecto.md, entradas de FASE 4). Se sacan aqui
para no duplicar la geometria entre el notebook y la app -- un cambio en
las dimensiones de pista solo hay que hacerlo en un sitio.

Dos funciones, una unidad cada una (a proposito, igual que
SHOT_COORD_CALIBRATION en src/features.py: cada fuente tiene su propia
unidad nativa, no se fuerza una conversion comun aqui):

- draw_court(): NBA, en decimas de pie (unidad nativa de nba_api), aro
  en el origen.
- draw_court_fiba(): Euroliga/EuroCup (y, de forma aproximada, WNBA una
  vez convertida a metros con SHOT_COORD_CALIBRATION) en metros, aro en
  el origen. Dimensiones FIBA (zona 4.9x5.8m, triple 6.75m/6.60m
  esquina) -- para WNBA esto es una aproximacion de las lineas de pista
  (las coordenadas de tiro en si SI estan calibradas correctamente, ver
  SHOT_COORD_CALIBRATION en src/features.py), no una reproduccion exacta
  del trazado real de basketball-reference.com.
"""
from __future__ import annotations

from matplotlib.patches import Arc, Rectangle


def draw_court(ax=None, color="black", lw=2):
    """Medio campo NBA en la unidad nativa de nba_api (decimas de pie,
    aro en el origen). Basado en las dimensiones oficiales NBA citadas
    en el plano (seccion 06)."""
    if ax is None:
        import matplotlib.pyplot as plt
        ax = plt.gca()

    elements = [
        Rectangle((-30, -7.5), 60, 1, linewidth=0, color=color),  # tablero
        Arc((0, 0), 15, 15, theta1=0, theta2=180, linewidth=lw, color=color),  # aro
        Rectangle((-80, -47.5), 160, 190, linewidth=lw, color=color, fill=False),  # zona
        Rectangle((-60, -47.5), 120, 190, linewidth=lw, color=color, fill=False),  # zona estrecha
        Arc((0, 142.5), 120, 120, theta1=0, theta2=180, linewidth=lw, color=color),  # tiro libre
        Arc((0, 142.5), 120, 120, theta1=180, theta2=360, linewidth=lw, color=color, linestyle="dashed"),
        Arc((0, 0), 475, 475, theta1=22, theta2=158, linewidth=lw, color=color),  # arco de 3
        Rectangle((-220, -47.5), 0, 140, linewidth=lw, color=color),  # esquina 3 izq.
        Rectangle((220, -47.5), 0, 140, linewidth=lw, color=color),  # esquina 3 dcha.
        Arc((0, 417.5), 120, 120, theta1=180, theta2=0, linewidth=lw, color=color),  # circulo medio campo
        Rectangle((-250, -47.5), 500, 470, linewidth=lw, color=color, fill=False),  # limites de pista
    ]
    for el in elements:
        ax.add_patch(el)
    ax.set_xlim(-260, 260)
    ax.set_ylim(-60, 430)
    ax.set_aspect("equal")
    ax.axis("off")
    return ax


def draw_court_fiba(ax=None, color="black", lw=2):
    """Medio campo FIBA en metros, aro en el origen -- dimensiones del
    plano (seccion 06), mismo criterio que draw_court() para NBA."""
    if ax is None:
        import matplotlib.pyplot as plt
        ax = plt.gca()

    baseline_y = -1.575
    ft_line_y = 4.225  # 5.8 - 1.575
    elements = [
        Rectangle((-0.9, -1.2), 1.8, 0.05, linewidth=0, color=color),  # tablero
        Arc((0, 0), 0.45, 0.45, theta1=0, theta2=180, linewidth=lw, color=color),  # aro
        Rectangle((-2.45, baseline_y), 4.9, ft_line_y - baseline_y, linewidth=lw, color=color, fill=False),  # zona
        Arc((0, ft_line_y), 3.6, 3.6, theta1=0, theta2=180, linewidth=lw, color=color),  # tiro libre
        Arc((0, ft_line_y), 3.6, 3.6, theta1=180, theta2=360, linewidth=lw, color=color, linestyle="dashed"),
        Arc((0, 0), 13.5, 13.5, theta1=12.1, theta2=167.9, linewidth=lw, color=color),  # arco de 3 (radio 6.75m)
        Rectangle((-6.60, baseline_y), 0, 1.415 - baseline_y, linewidth=lw, color=color),  # esquina 3 izq.
        Rectangle((6.60, baseline_y), 0, 1.415 - baseline_y, linewidth=lw, color=color),  # esquina 3 dcha.
        Arc((0, 14.0 - 1.575), 3.6, 3.6, theta1=180, theta2=0, linewidth=lw, color=color),  # circulo medio campo
        Rectangle((-7.5, baseline_y), 15, 14.0 - 1.575 - baseline_y, linewidth=lw, color=color, fill=False),  # limites
    ]
    for el in elements:
        ax.add_patch(el)
    ax.set_xlim(-7.9, 7.9)
    ax.set_ylim(-1.9, 13.0)
    ax.set_aspect("equal")
    ax.axis("off")
    return ax
