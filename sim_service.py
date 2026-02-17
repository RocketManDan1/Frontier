import os
import threading
import time

GAME_TIME_SCALE = float(os.environ.get("GAME_TIME_SCALE", "48"))
RESET_GAME_EPOCH_S = 946684800.0  # 2000-01-01T00:00:00Z
_REAL_TIME_ANCHOR_S = time.time()
_GAME_TIME_ANCHOR_S = _REAL_TIME_ANCHOR_S
_SIMULATION_PAUSED = False
_SIMULATION_LOCK = threading.Lock()


def game_now_s() -> float:
    now_real_s = time.time()
    with _SIMULATION_LOCK:
        if _SIMULATION_PAUSED:
            return _GAME_TIME_ANCHOR_S
        real_elapsed_s = now_real_s - _REAL_TIME_ANCHOR_S
        return _GAME_TIME_ANCHOR_S + (real_elapsed_s * GAME_TIME_SCALE)


def simulation_paused() -> bool:
    with _SIMULATION_LOCK:
        return _SIMULATION_PAUSED


def effective_time_scale() -> float:
    return 0.0 if simulation_paused() else GAME_TIME_SCALE


def set_simulation_paused(paused: bool) -> None:
    global _REAL_TIME_ANCHOR_S, _GAME_TIME_ANCHOR_S, _SIMULATION_PAUSED

    now_real_s = time.time()
    with _SIMULATION_LOCK:
        if _SIMULATION_PAUSED:
            current_game_s = _GAME_TIME_ANCHOR_S
        else:
            real_elapsed_s = now_real_s - _REAL_TIME_ANCHOR_S
            current_game_s = _GAME_TIME_ANCHOR_S + (real_elapsed_s * GAME_TIME_SCALE)

        _GAME_TIME_ANCHOR_S = current_game_s
        _REAL_TIME_ANCHOR_S = now_real_s
        _SIMULATION_PAUSED = bool(paused)


def reset_simulation_clock() -> None:
    global _REAL_TIME_ANCHOR_S, _GAME_TIME_ANCHOR_S, _SIMULATION_PAUSED

    now_real_s = time.time()
    with _SIMULATION_LOCK:
        _REAL_TIME_ANCHOR_S = now_real_s
        _GAME_TIME_ANCHOR_S = RESET_GAME_EPOCH_S
        _SIMULATION_PAUSED = False
