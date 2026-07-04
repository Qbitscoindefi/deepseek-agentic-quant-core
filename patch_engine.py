import shutil
from pathlib import Path

ENGINE_FILE = "trading_engine.py"
BACKUP_FILE = "trading_engine_backup.py"

# =========================================================
# BACKUP
# =========================================================

shutil.copy2(ENGINE_FILE, BACKUP_FILE)
print(f"[OK] Backup creado: {BACKUP_FILE}")

# =========================================================
# LEER ENGINE
# =========================================================

path = Path(ENGINE_FILE)
content = path.read_text(encoding="utf-8")

# =========================================================
# PATCH 1 - IMPORT DEQUE
# =========================================================

if "from collections import deque" not in content:
    content = "from collections import deque\n" + content
    print("[OK] deque importado")

# =========================================================
# PATCH 2 - MEMORIA HFT
# =========================================================

target_init = "self.client = client"

replacement_init = """self.client = client

        # =========================
        # HFT IMPULSE MEMORY
        # =========================
        self.impulse_history = deque(maxlen=5)
        self.last_impulse = 0.0"""

if target_init in content:
    content = content.replace(target_init, replacement_init)
    print("[OK] memoria HFT agregada")

# =========================================================
# PATCH 3 - BLOQUE IMPULSO
# =========================================================

start_marker = "# =========================\n# IMPULSO DINÁMICO HFT"
end_marker = "# Positivo = alcista, Negativo = bajista"

if start_marker in content and end_marker in content:
    start_index = content.index(start_marker)

    end_index = content.index(end_marker)
    end_index += len(end_marker)

    replacement_block = """
        # =========================
        # IMPULSO DINÁMICO HFT
        # =========================

        impulse_raw = (
            (current_price - price_n_ago) / atr
            if atr > 0 else 0.0
        )

        # Historial
        self.impulse_history.append(impulse_raw)

        # Velocidad
        impulse_velocity = (
            impulse_raw - self.last_impulse
        )

        self.last_impulse = impulse_raw

        # Persistencia
        persistence_score = 0.0

        if len(self.impulse_history) >= 3:

            recent = list(self.impulse_history)[-3:]

            # aceleración progresiva
            if recent[2] > recent[1] > recent[0]:
                persistence_score = 1.0

            # impulso fuerte sostenido
            elif min(recent) > 0.6:
                persistence_score = 0.7

            # decay
            elif recent[2] < recent[1] < recent[0]:
                persistence_score = -1.0

        # score compuesto
        impulse_score_dynamic = (
            (impulse_raw * 0.55) +
            (impulse_velocity * 0.30) +
            (persistence_score * 0.15)
        )

        # Positivo = alcista, Negativo = bajista
"""

    content = content[:start_index] + replacement_block + content[end_index:]

    print("[OK] bloque impulso reparado")

# =========================================================
# PATCH 4 - DECAY PROTECTION
# =========================================================

target_decay = "if impulse_raw < MIN_IMPULSE_ATR:"

replacement_decay = """
            if persistence_score < 0:
                return result(
                    'NEUTRAL',
                    0,
                    f'IMPULSE_DECAY:{impulse_raw:.3f}'
                )

            if impulse_raw < MIN_IMPULSE_ATR:
"""

if target_decay in content:
    content = content.replace(target_decay, replacement_decay)
    print("[OK] decay protection agregado")

# =========================================================
# PATCH 5 - SCORE LONG
# =========================================================

target_long = "long_impulse_score = max(0.0, min(1.0, impulse_raw / 1.0))"

replacement_long = """
        long_impulse_score = max(
            0.0,
            min(1.0, impulse_score_dynamic)
        )
"""

if target_long in content:
    content = content.replace(target_long, replacement_long)
    print("[OK] score LONG dinámico agregado")

# =========================================================
# PATCH 6 - SCORE SHORT
# =========================================================

target_short = "short_impulse_score = max(0.0, min(1.0, -impulse_raw / 1.0))"

replacement_short = """
        short_impulse_score = max(
            0.0,
            min(1.0, -impulse_score_dynamic)
        )
"""

if target_short in content:
    content = content.replace(target_short, replacement_short)
    print("[OK] score SHORT dinámico agregado")

# =========================================================
# GUARDAR
# =========================================================

path.write_text(content, encoding="utf-8")

print("\n===================================")
print("PATCH HFT REPARADO Y APLICADO")
print("===================================")
