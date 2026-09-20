import os
import json
import cv2
import PySimpleGUI as sg

IMAGE_TYPES = (".png", ".jpeg", ".jpg", ".bmp")
VIDEO_TYPES = (".mp4", ".mkv", ".avi", ".mov")

def valid_file(path):
    """Vérifie si le fichier existe et possède une extension image ou vidéo valide."""
    if not isinstance(path, str):
        return False
    extension = path[path.rfind("."):].lower()
    return os.path.isfile(path) and (extension in IMAGE_TYPES or extension in VIDEO_TYPES)

def get_files(filepaths):
    """Générateur qui filtre et retourne uniquement les fichiers valides."""
    input_filepaths = [f for f in filepaths if valid_file(f)]
    for f in input_filepaths:
        yield f

def get_tasks(filepaths, file_markers):
    """Génère les tâches de traitement (image complète, vidéo complète ou segments de vidéo découpés)."""
    for f in filepaths:
        if not valid_file(f):
            continue
        ext = f[f.rfind("."):].lower()
        base_name = os.path.basename(f)
        name_without_ext, _ = os.path.splitext(base_name)
        
        if ext in IMAGE_TYPES:
            yield {"type": "image", "file": f, "name_without_ext": name_without_ext, "ext": ext, "seq": 1, "total_seqs": 1}
        elif ext in VIDEO_TYPES:
            markers = file_markers.get(f, [])
            if not markers or len(markers) < 2:
                yield {"type": "video_full", "file": f, "name_without_ext": name_without_ext, "ext": ext, "seq": 1, "total_seqs": 1}
            else:
                cap = cv2.VideoCapture(f)
                fps = cap.get(cv2.CAP_PROP_FPS) or 30
                cap.release()
                seq_num = 1
                total_seqs = len(markers) // 2
                for i in range(0, len(markers) - 1, 2):
                    start_f = markers[i]
                    end_f = markers[i + 1]
                    yield {
                        "type": "video_segment",
                        "file": f,
                        "name_without_ext": name_without_ext,
                        "ext": ext,
                        "start": start_f,
                        "end": end_f,
                        "seq": seq_num,
                        "fps": fps,
                        "total_seqs": total_seqs
                    }
                    seq_num += 1

def get_selected_filepath(window):
    """Retourne le fichier sélectionné dans la liste ou le premier disponible."""
    filepaths = window["__INPUT_FILE_LIST__"].get_list_values()
    selected = window["__INPUT_FILE_LIST__"].get()
    if selected:
        return selected[0]
    elif filepaths:
        return filepaths[0]
    return None

def check_and_save_current_project(window, current_project_name, file_markers):
    """Gère la sauvegarde de l'état du projet en cours ou demande confirmation à l'utilisateur."""
    current_files = window["__INPUT_FILE_LIST__"].get_list_values()
    ops_order = window["__OPS_LIST__"].get_list_values()
    applied_luts = window["__APPLIED_LUT_LIST__"].get_list_values()

    if current_project_name:
        os.makedirs("Projects", exist_ok=True)
        project_path = os.path.join("Projects", f"{current_project_name}.cfg")
        data_to_save = {
            "project_name": current_project_name,
            "files": current_files,
            "markers": {f: file_markers[f] for f in current_files if f in file_markers and file_markers[f]},
            "ops_order": ops_order,
            "applied_luts": applied_luts
        }
        try:
            with open(project_path, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f)
            with open("list.cfg", "w", encoding="utf-8") as f:
                json.dump(data_to_save, f)
        except Exception as e:
            print(f"Error saving project {current_project_name}: {e}")
        return True
    elif len(current_files) > 0:
        choice = sg.popup_yes_no_cancel("Do you want to save the current project before proceeding?", title="Save Project")
        if choice == "Yes":
            name = sg.popup_get_text("Enter project name:", title="Save Project")
            if name and name.strip():
                save_name = name.strip()
                os.makedirs("Projects", exist_ok=True)
                project_path = os.path.join("Projects", f"{save_name}.cfg")
                data_to_save = {
                    "project_name": save_name,
                    "files": current_files,
                    "markers": {f: file_markers[f] for f in current_files if f in file_markers and file_markers[f]},
                    "ops_order": ops_order,
                    "applied_luts": applied_luts
                }
                try:
                    with open(project_path, "w", encoding="utf-8") as f:
                        json.dump(data_to_save, f)
                    with open("list.cfg", "w", encoding="utf-8") as f:
                        json.dump(data_to_save, f)
                except Exception as e:
                    print(f"Error saving project {save_name}: {e}")
                return True
            else:
                return False
        elif choice == "No":
            return True
        else:
            return False
    return True
