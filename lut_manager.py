import os
import numpy as np
from correct import load_cube_lut

LUTS_DIR = "LUTs"
os.makedirs(LUTS_DIR, exist_ok=True)

# Cache mémoire: { filepath: (mtime, baked_array) }
_lut_cache = {}

def get_luts_list():
    """Retourne la liste de tous les fichiers .cube présents dans le dossier LUTs."""
    if not os.path.exists(LUTS_DIR):
        return []
    return [f for f in os.listdir(LUTS_DIR) if f.lower().endswith('.cube')]

def get_available_luts_list(applied_luts):
    """Retourne la liste des LUTs disponibles qui ne sont pas encore appliquées."""
    all_luts = get_luts_list()
    return [f for f in all_luts if f not in applied_luts]

def _npz_path_for(cube_path):
    """Retourne le chemin du fichier de cache .npz pour un fichier .cube donné."""
    return cube_path.rsplit('.', 1)[0] + '.lut.npz'

def is_lut_cached(path):
    """Vérifie si la LUT est déjà dans le cache mémoire et à jour."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    cached = _lut_cache.get(path)
    return cached is not None and cached[0] == mtime

def _save_baked_to_disk(baked, cube_path):
    """Sauvegarde la table LUT pré-calculée sous forme de fichier .npz compressé."""
    npz_path = _npz_path_for(cube_path)
    try:
        np.savez_compressed(npz_path, data=baked)
    except Exception as e:
        print(f"Could not save LUT cache to {npz_path}: {e}")

def load_lut_cached(path):
    """Charge et met en cache une LUT pré-calculée.
    Ordre de priorité : cache mémoire > cache disque .npz > calcul depuis le .cube.
    """
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _lut_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]

    npz_path = _npz_path_for(path)
    if os.path.exists(npz_path):
        try:
            npz_mtime = os.path.getmtime(npz_path)
            cube_mtime = os.path.getmtime(path)
            if npz_mtime >= cube_mtime:
                baked = np.load(npz_path)['data']
                _lut_cache[path] = (mtime, baked)
                return baked
        except Exception as e:
            print(f"Could not load LUT cache {npz_path}: {e}")

    try:
        baked = load_cube_lut(path)
        _lut_cache[path] = (mtime, baked)
        _save_baked_to_disk(baked, path)
        return baked
    except Exception as e:
        print(f"Error loading LUT {path}: {e}")
        return None

def set_window_cursor(window, cursor):
    """Change le curseur de la fenêtre principale et de ses enfants."""
    try:
        root = window.TKroot
        root.config(cursor=cursor)
        for widget in root.winfo_children():
            try:
                widget.config(cursor=cursor)
            except Exception:
                pass
        root.update()
    except Exception:
        pass

def get_selected_lut_arrays(window):
    """Retourne la liste des tableaux LUT chargés (depuis le cache) :
    - En premier : les LUTs de la liste appliquée, dans l'ordre.
    - Ensuite : la LUT sélectionnée dans la liste disponible (si présente).
    Chapeaute la modification du curseur si une compilation LUT est nécessaire.
    """
    applied = window["__APPLIED_LUT_LIST__"].get_list_values()
    selected = window["__LUT_LIST__"].get()
    names = list(applied) + [s for s in selected if s not in applied]

    paths = [os.path.join(LUTS_DIR, name) for name in names]
    needs_bake = any(not is_lut_cached(p) for p in paths if os.path.exists(p))

    if needs_bake:
        set_window_cursor(window, 'watch')

    try:
        luts = []
        for path in paths:
            if os.path.exists(path):
                baked = load_lut_cached(path)
                if baked is not None:
                    luts.append(baked)
        return luts
    finally:
        if needs_bake:
            set_window_cursor(window, '')
