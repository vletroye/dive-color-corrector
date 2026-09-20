import PySimpleGUI as sg
import os
import time
import subprocess
import cv2
import numpy as np
import json
import shutil
import webbrowser
import imageio_ffmpeg
from correct import correct, correct_image, analyze_video, process_video, get_frame_preview, get_image_preview
from logo.logo import LOGO

from lut_manager import LUTS_DIR, get_luts_list, get_available_luts_list, get_selected_lut_arrays
from project_manager import IMAGE_TYPES, VIDEO_TYPES, valid_file, get_files, get_tasks, get_selected_filepath, check_and_save_current_project
from timeline_manager import (
    undo_stack, redo_stack, is_frame_inside_sequence, update_add_button_state,
    update_undo_redo_buttons, push_undo_state, update_marker_buttons, draw_timeline
)
from gui_layout import DEFAULT_OPS, has_nvidia_gpu, setup_theme, get_btn_style, show_help_window, build_main_layout

FAST_STEP = 10
has_nvidia = has_nvidia_gpu()

setup_theme()

layout = build_main_layout(has_nvidia)

window = sg.Window("DCC: Dive Color Corrector", layout, finalize=True, resizable=True, return_keyboard_events=True)

import tkinter as tk
import io


# Configuration de l'expansion Tkinter dans le panneau de droite
_viewer_col_widget = window['__VIEWER_COL__'].Widget
_viewer_col_widget.pack_configure(fill='both', expand=True)

# Les enfants directes de la colonne viewer sont les rangées de la grille
_viewer_children = _viewer_col_widget.winfo_children()
if _viewer_children:
    # 1er enfant (zone preview / info) : prend TOUT l'espace vertical disponible
    _viewer_children[0].pack_configure(fill='both', expand=True)
    # Enfants suivants (play, timeline, luts) : compacts en bas, l'un contre l'autre
    for _child in _viewer_children[1:]:
        _child.pack_configure(fill='x', expand=False)

# S'assurer que le frame de preview et son canvas remplissent bien le 1er conteneur
window['__PREVIEW_FRAME__'].Widget.pack_configure(fill='both', expand=True)
window['__INFO__'].Widget.pack_configure(fill='both', expand=True)

_canvas_widget = window['__PREVIEW_CANVAS__'].TKCanvas
_preview_photo_ref = None  # évite le garbage collection de PhotoImage
_resize_timer = None

def _on_canvas_configure(event):
    """Redessine la preview quand le canvas change de taille avec un petit anti-rebond."""
    global _resize_timer
    if _last_preview_img is not None:
        if _resize_timer is not None:
            window.TKroot.after_cancel(_resize_timer)
        _resize_timer = window.TKroot.after(50, lambda: redraw_preview(window))

_canvas_widget.bind('<Configure>', _on_canvas_configure)

def _get_controls_height(window):
    """Retourne la hauteur totale des contrôles visibles sous la preview."""
    h = 0
    for key in ['__GENERAL_PLAY_CONTAINER__', '__TIMELINE_CONTAINER__', '__LUTS_CONTAINER__']:
        elem = window[key]
        if elem.visible:
            wh = elem.Widget.winfo_height()
            if wh > 1:
                h += wh
    return h

def get_preview_size(window, img_w, img_h):
    window.TKroot.update_idletasks()
    canvas = window['__PREVIEW_CANVAS__'].TKCanvas
    avail_w = canvas.winfo_width()
    avail_h = canvas.winfo_height()

    if avail_w < 10 or avail_h < 10:
        viewer_w = window['__VIEWER_COL__'].Widget.winfo_width()
        viewer_h = window['__VIEWER_COL__'].Widget.winfo_height()
        if viewer_w < 10:
            viewer_w = window.TKroot.winfo_width() - window['__MEDIA_FRAME__'].Widget.winfo_width() - 60
        ctrl_h = _get_controls_height(window)
        if ctrl_h < 10:
            ctrl_h = 250
        avail_w = max(100, viewer_w - 20)
        avail_h = max(100, viewer_h - ctrl_h - 20)

    aspect = img_w / img_h
    new_w = avail_w
    new_h = int(new_w / aspect)
    if new_h > avail_h:
        new_h = avail_h
        new_w = int(new_h * aspect)
    return max(1, new_w), max(1, new_h)

_last_preview_img = None

def draw_preview_on_canvas(window, img_bgr):
    """Dessine un frame OpenCV (BGR) sur le canvas Tkinter en remplissant l'espace disponible."""
    global _preview_photo_ref, _last_preview_img
    _last_preview_img = img_bgr
    h, w = img_bgr.shape[:2]
    new_w, new_h = get_preview_size(window, w, h)
    resized = cv2.resize(img_bgr, (new_w, new_h))
    png_bytes = cv2.imencode('.png', resized)[1].tobytes()
    import base64
    photo = tk.PhotoImage(data=base64.b64encode(png_bytes))
    _preview_photo_ref = photo
    canvas = window['__PREVIEW_CANVAS__'].TKCanvas
    canvas.delete('all')
    cw = canvas.winfo_width()
    ch = canvas.winfo_height()
    x = max(0, (cw - new_w) // 2)
    y = max(0, (ch - new_h) // 2)
    canvas.create_image(x, y, anchor='nw', image=photo)

def redraw_preview(window):
    """Redessine la preview courante si une image existe."""
    if _last_preview_img is not None and window['__PREVIEW_CANVAS__'].visible:
        draw_preview_on_canvas(window, _last_preview_img)

master_ops_order = list(DEFAULT_OPS)

def get_action_name(label):
    return label[1:-1] if label.startswith("[") and label.endswith("]") else label

def get_action_order(window):
    """Returns the list of active ops labels currently displayed in the Treatments listbox."""
    return window["__OPS_LIST__"].get_list_values()

def get_active_action_order(window):
    """Returns the list of active ops labels currently displayed in the Treatments listbox."""
    return window["__OPS_LIST__"].get_list_values()

def update_action_labels(window, values=None):
    global master_ops_order
    
    try:
        use_clahe = window["__USE_CLAHE__"].get()
        use_brightness = window["__USE_BRIGHTNESS__"].get()
        use_saturation = window["__USE_SATURATION__"].get()
    except Exception:
        use_clahe = False
        use_brightness = False
        use_saturation = False

    applied_luts = window["__APPLIED_LUT_LIST__"].get_list_values()
    selected_luts = window["__LUT_LIST__"].get()
    has_luts = len(applied_luts) > 0 or len(selected_luts) > 0
    
    active_states = {
        "Dive Color": True,
        "Red Strength": True,
        "Hue Shift": True,
        "CLAHE": use_clahe,
        "Brightness": use_brightness,
        "Contrast": use_brightness,
        "Saturation": use_saturation,
        "LUTs": has_luts,
    }
    
    current_displayed = [get_action_name(l) for l in window["__OPS_LIST__"].get_list_values()]
    
    new_master = []
    for op in current_displayed:
        if op not in new_master:
            new_master.append(op)
    for op in master_ops_order:
        if op not in new_master:
            new_master.append(op)
    master_ops_order = new_master

    displayed = [op for op in master_ops_order if active_states.get(op, False)]
    window["__OPS_LIST__"].update(values=displayed)

update_action_labels(window)

window["__TIMELINE__"].bind('<ButtonPress-1>', '_PRESS')
window["__TIMELINE__"].bind('<ButtonRelease-1>', '_RELEASE')
window["__TIMELINE__"].Widget.bind('<Configure>', lambda e: window.write_event_value("__TIMELINE_RESIZE__", None))

graphical_buttons = ["__CORRECT__", "__CANCEL__", "__OPEN_OUTPUT__", "__ADD_MARK__", "__DEL_MARK__", "__FULL_PREVIEW__", "__SAVE_FRAME__", "__UNDO__", "__REDO__", "__NEW_PROJECT__", "__LOAD_PROJECT__", "__SAVE_PROJECT__", "__RESET_SETTINGS__"]
for btn_key in ["__MARKER_FAST_REW__", "__MARKER_REW__", "__MARKER_FWD__", "__MARKER_FAST_FWD__"] + graphical_buttons:
    widget = window[btn_key].Widget
    widget.bind('<ButtonPress-1>', lambda e, k=btn_key: window.write_event_value(k + "_PRESS", None))
    widget.bind('<ButtonRelease-1>', lambda e, k=btn_key: window.write_event_value(k + "_RELEASE", None))

window["__FULL_PREVIEW__"].Widget.bind('<ButtonPress-1>', lambda e: window.write_event_value("__FULL_PREVIEW___PRESS", None))
window["__FULL_PREVIEW__"].Widget.bind('<ButtonRelease-1>', lambda e: window.write_event_value("__FULL_PREVIEW___RELEASE", None))

window.bind('<Control-z>', '__UNDO__')
window.bind('<Control-Z>', '__UNDO__')
window.bind('<Control-y>', '__REDO__')
window.bind('<Control-Y>', '__REDO__')
window.bind('<Left>', '__KBD_LEFT')
window.bind('<Right>', '__KBD_RIGHT')
window.bind('<Control-Left>', '__KBD_CTRL_LEFT')
window.bind('<Control-Right>', '__KBD_CTRL_RIGHT')

window["__FRAME_SLIDER__"].Widget.config(length=1080)

# --- BINDING CLIC DROIT LISTBOX : sélectionne l'item sous le curseur avant l'apparition du menu ---
def on_listbox_right_click(event):
    widget = window["__INPUT_FILE_LIST__"].Widget
    idx = widget.nearest(event.y)
    if idx >= 0:
        widget.selection_clear(0, 'end')
        widget.selection_set(idx)
        widget.activate(idx)

window["__INPUT_FILE_LIST__"].Widget.bind('<Button-3>', on_listbox_right_click)

def valid_file(path):
    extension = path[path.rfind("."):].lower() 
    return os.path.isfile(path) and (extension in IMAGE_TYPES or extension in VIDEO_TYPES)

def get_files(filepaths):
    input_filepaths = [f for f in filepaths  if valid_file(f)]
    for f in input_filepaths:
        yield f

def get_tasks(filepaths, file_markers):
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
                    yield {"type": "video_segment", "file": f, "name_without_ext": name_without_ext, "ext": ext, "start": start_f, "end": end_f, "seq": seq_num, "fps": fps, "total_seqs": total_seqs}
                    seq_num += 1

def get_selected_filepath(window):
    filepaths = window["__INPUT_FILE_LIST__"].get_list_values()
    selected = window["__INPUT_FILE_LIST__"].get()
    if selected:
        return selected[0]
    elif filepaths:
        return filepaths[0]
    return None

def is_frame_inside_sequence(frame, markers):
    for i in range(0, len(markers) - 1, 2):
        start_m = markers[i]
        end_m = markers[i + 1]
        if start_m < frame < end_m:
            return True
    return False

def update_add_button_state(window, markers, current_frame, fps, frame_count):
    disabled = False
    if frame_count and current_frame > frame_count - int(3 * fps):
        disabled = True
    elif current_frame in markers or is_frame_inside_sequence(current_frame, markers):
        disabled = True
    else:
        for i in range(0, len(markers), 2):
            start_m = markers[i]
            if current_frame <= start_m and (start_m - current_frame) < 50:
                disabled = True
                break
    
    img_path = os.path.join("assets", "add_mark.png")
    if os.path.exists(img_path):
        window["__ADD_MARK__"].update(disabled=disabled)
    else:
        window["__ADD_MARK__"].update(disabled=disabled, button_color=('#555555' if disabled else '#28a745'))

def update_undo_redo_buttons(window):
    global undo_stack, redo_stack
    dis_undo = len(undo_stack) == 0
    dis_redo = len(redo_stack) == 0
    
    img_undo = os.path.join("assets", "undo.png")
    img_redo = os.path.join("assets", "redo.png")
    
    if os.path.exists(img_undo):
        window["__UNDO__"].update(disabled=dis_undo)
        window["__REDO__"].update(disabled=dis_redo)
    else:
        window["__UNDO__"].update(disabled=dis_undo, button_color=('#555555' if dis_undo else '#007acc'))
        window["__REDO__"].update(disabled=dis_redo, button_color=('#555555' if dis_redo else '#007acc'))

def push_undo_state(markers):
    global undo_stack, redo_stack
    if not undo_stack or undo_stack[-1] != markers:
        undo_stack.append(list(markers))
        redo_stack.clear()
        update_undo_redo_buttons(window)

def draw_timeline(window, markers, selected_idx, current_frame, frame_count, only_playhead=False):
    graph = window["__TIMELINE__"]
    
    if not only_playhead:
        graph.erase()
    else:
        # Only erase the playhead line if we have a reference to it
        if hasattr(graph, '_playhead_id') and graph._playhead_id:
            graph.delete_figure(graph._playhead_id)
            
    if frame_count <= 0:
        return
        
    if not only_playhead:
        # Update CanvasSize to match actual widget size so change_coordinates works correctly
        window.TKroot.update_idletasks()
        actual_width = graph.Widget.winfo_width()
        if actual_width > 10:
            graph.CanvasSize = (actual_width, 30)
            
        # Add a small margin to both sides of the coordinate system
        # to account for the slider's thumb width.
        # The slider thumb is roughly 15 pixels wide.
        graph_width = graph.CanvasSize[0] if graph.CanvasSize[0] else 1070
        # So the margin in "frame units" is roughly (15 / graph_width) * frame_count / 2
        margin = max(1, int(frame_count * (15 / graph_width) / 2))
        graph.change_coordinates((1 - margin, 0), (frame_count + margin, 30))
        
        graph.draw_line((1, 15), (frame_count, 15), color='#444444', width=4)
        
        marker_w = max(1, frame_count * 0.010) 
        
        for i, m in enumerate(markers):
            color = '#4CAF50' if i % 2 == 0 else '#F44336'
            is_selected = (i == selected_idx)
            outline = 'white' if is_selected else color
            line_w = 2 if is_selected else 1
            
            if i % 2 == 0:
                # Draw '[' shape for start marker
                graph.draw_line((m, 5), (m, 25), color=outline, width=line_w)
                graph.draw_line((m, 5), (m + marker_w, 5), color=outline, width=line_w)
                graph.draw_line((m, 25), (m + marker_w, 25), color=outline, width=line_w)
            else:
                # Draw ']' shape for end marker
                graph.draw_line((m, 5), (m, 25), color=outline, width=line_w)
                graph.draw_line((m, 5), (m - marker_w, 5), color=outline, width=line_w)
                graph.draw_line((m, 25), (m - marker_w, 25), color=outline, width=line_w)

    # Ensure the playhead line doesn't draw past the end of the graph
    draw_frame = min(current_frame, frame_count)
    graph._playhead_id = graph.draw_line((draw_frame, 0), (draw_frame, 30), color='white', width=1)

def update_marker_buttons(window, selected_idx):
    has_selection = selected_idx is not None
    can_delete = has_selection and (selected_idx % 2 == 0)
    
    img_del = os.path.join("assets", "delete.png")
    if os.path.exists(img_del):
        window["__DEL_MARK__"].update(disabled=not can_delete)
    else:
        window["__DEL_MARK__"].update(disabled=not can_delete, button_color=('#555555' if not can_delete else '#dc3545'))
    
    for k in ["__MARKER_FAST_REW__", "__MARKER_REW__", "__MARKER_FWD__", "__MARKER_FAST_FWD__"]:
        window[k].update(disabled=False)

def update_general_play_buttons(window, gen_play, gen_rew, play_speed):
    play_text = f"Play > ({play_speed}x)" if gen_play else "Play >"
    rew_text = f"< Rewind ({play_speed}x)" if gen_rew else "< Rewind"
    window["__GEN_PLAY__"].update(text=play_text)
    window["__GEN_REW__"].update(text=rew_text)

def update_raw_preview(window, cap, frame_number):
    if cap is None or not cap.isOpened():
        return
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_number - 1))
    ret, frame = cap.read()
    if ret and frame is not None:
        draw_preview_on_canvas(window, frame)

def show_test_frame(window, values, target_frame=None):
    filepaths = [x for x in window["__INPUT_FILE_LIST__"].get_list_values()]
    selected = window["__INPUT_FILE_LIST__"].get()
    
    if not filepaths:
        window["__STATUS__"].update("No files available for preview")
        window["__SHOW_PREVIEW__"].update(False)
        window["__SAVE_FRAME__"].update(disabled=True)
        return 1, 1, 30
        
    f = selected[0] if selected else filepaths[0]
    extension = f[f.rfind("."):].lower()
    
    use_clahe = values.get("__USE_CLAHE__", False)
    clahe_clip = values.get("__CLAHE_CLIP__", 2.0)
    use_saturation = values.get("__USE_SATURATION__", False)
    saturation_val = values.get("__SATURATION__", 1.0)
    use_brightness = values.get("__USE_BRIGHTNESS__", False)
    brightness_val = values.get("__BRIGHTNESS__", 0)
    contrast_val = values.get("__CONTRAST__", 1.0)
    red_str = values.get("__RED_STRENGTH__", 60)
    hue_shift = values.get("__HUE_SHIFT__", 120)
    
    luts = get_selected_lut_arrays(window)
    lut_only = window["__LUT_ONLY_BTN__"].ButtonColor[1] == '#28a745' if hasattr(window["__LUT_ONLY_BTN__"], 'ButtonColor') else False
    ops_order = get_active_action_order(window)
    
    if target_frame is None:
        window["__STATUS__"].update("Generating preview...")
    window.refresh() 
    
    ret_frame_count = 1
    ret_fps = 30

    if extension in VIDEO_TYPES:
        try:
            cap = cv2.VideoCapture(f)
            ret_fps = cap.get(cv2.CAP_PROP_FPS) or 30
            cap.release()

            if target_frame is None:
                markers = file_markers.get(f, [])

                if markers and len(markers) >= 2:
                    target_frame = markers[0]
                else:
                    try:
                        val = int(window["__FRAME_SLIDER__"].get())
                        target_frame = val if val > 0 else 1
                    except Exception:
                        target_frame = 1

            preview_bytes, frame_count = get_frame_preview(f, target_frame, use_clahe, red_str, hue_shift, clahe_clip, use_saturation, saturation_val, use_brightness, brightness_val, contrast_val, luts, lut_only, ops_order)
            ret_frame_count = frame_count

            if preview_bytes:
                img_array = np.frombuffer(preview_bytes, dtype=np.uint8)
                img_mat = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

                if frame_count > 0:
                    window["__FRAME_SLIDER__"].update(range=(1, frame_count), value=target_frame, visible=True)
                    window["__GENERAL_PLAY_CONTAINER__"].update(visible=True)
                    window["__TIMELINE_CONTAINER__"].update(visible=True)
                    window["__LUTS_CONTAINER__"].update(visible=True)

                window["__INFO__"].update(visible=False)
                window["__PREVIEW_FRAME__"].update(visible=True)
                window["__PREVIEW_CANVAS__"].update(visible=True)
                window.TKroot.update_idletasks()
                draw_preview_on_canvas(window, img_mat)

                if frame_count > 0:
                    if not os.path.exists(os.path.join("assets", "save_frame.png")):
                        window["__SAVE_FRAME__"].update(disabled=False, button_color=('#ffffff', '#007acc'))
                    else:
                        window["__SAVE_FRAME__"].update(disabled=False)

                    for k in ["__GEN_REW__", "__GEN_STOP__", "__GEN_PLAY__", "__MARKER_FAST_REW__", "__MARKER_REW__", "__MARKER_FWD__", "__MARKER_FAST_FWD__"]:
                        window[k].update(disabled=False)

                    if not os.path.exists(os.path.join("assets", "full_preview.png")):
                        window["__FULL_PREVIEW__"].update(disabled=False, button_color=('#ffffff', '#007acc'))
                    else:
                        window["__FULL_PREVIEW__"].update(disabled=False)

                if target_frame is None:
                    window["__STATUS__"].update("Preview displayed")
            else:
                window["__STATUS__"].update("Error: Could not read frame")
        except Exception as e:
            window["__STATUS__"].update(f"Error generating preview: {e}")
            
    elif extension in IMAGE_TYPES:
        window["__FRAME_SLIDER__"].update(visible=False)
        window["__GENERAL_PLAY_CONTAINER__"].update(visible=False)
        window["__TIMELINE_CONTAINER__"].update(visible=False)
        window["__SAVE_FRAME__"].update(disabled=True)
        try:
            preview_bytes = get_image_preview(f, use_clahe, red_str, hue_shift, clahe_clip, use_saturation, saturation_val, use_brightness, brightness_val, contrast_val, luts, lut_only, ops_order)
            if preview_bytes:
                img_array = np.frombuffer(preview_bytes, dtype=np.uint8)
                img_mat = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                window["__INFO__"].update(visible=False)
                window["__PREVIEW_FRAME__"].update(visible=True)
                window["__PREVIEW_CANVAS__"].update(visible=True)
                window.TKroot.update_idletasks()
                draw_preview_on_canvas(window, img_mat)
                if target_frame is None:
                    window["__STATUS__"].update("Preview displayed")
        except Exception as e:
            window["__STATUS__"].update(f"Error generating preview: {e}")

    return (target_frame if target_frame else (ret_frame_count//2)), ret_frame_count, ret_fps

def set_ui_processing_state(window, processing, values):
    elements = [
        "__CORRECT__", "__OPEN_OUTPUT__", "__NEW_PROJECT__", "__LOAD_PROJECT__", "__SAVE_PROJECT__", "__INPUT_FILES__", "__FILES_BROWSE_BTN__", "__INPUT_FILE_LIST__",
        "__OUTPUT_BROWSE__", "__OUTPUT_PREFIX__", "__IS_SUFFIX__", "__KEEP_SOUND__",
        "__USE_NVENC__", "__CREATE_DEMO__", "__USE_CLAHE__", "__USE_SATURATION__", "__USE_BRIGHTNESS__", 
        "__RED_STRENGTH__", "__HUE_SHIFT__", "__RESET_SETTINGS__", "__SAVE_FRAME__"
    ]
    for key in elements:
        window[key].update(disabled=processing)
        
        if key == "__CORRECT__" and not os.path.exists(os.path.join("assets", "start.png")):
            window[key].update(button_color=('#555555', '#3d3d3d') if processing else ('#ffffff', '#28a745'))
        if key in ["__NEW_PROJECT__", "__LOAD_PROJECT__", "__SAVE_PROJECT__"]:
            btn_name = key.replace("__", "").replace("_PROJECT", "").lower()
            if not os.path.exists(os.path.join("assets", f"{btn_name}.png")):
                window[key].update(button_color=('#555555', '#3d3d3d') if processing else ('#ffffff', '#3d3d3d'))
            
    if not os.path.exists(os.path.join("assets", "cancel.png")):
        window["__CANCEL__"].update(disabled=not processing, button_color=('#555555', '#3d3d3d') if not processing else ('#ffffff', '#dc3545'))
    else:
        window["__CANCEL__"].update(disabled=not processing)
    
    if processing:
        window["__CLAHE_CLIP__"].update(disabled=True)
        window["__CLAHE_CLIP_TEXT__"].update(text_color='gray')
        window["__SATURATION__"].update(disabled=True)
        window["__SATURATION_TEXT__"].update(text_color='gray')
        window["__BRIGHTNESS__"].update(disabled=True)
        window["__BRIGHTNESS_TEXT__"].update(text_color='gray')
        window["__CONTRAST__"].update(disabled=True)
        window["__CONTRAST_TEXT__"].update(text_color='gray')
        
        window["__PREVIEW_FRAME__"].update(visible=True)
        window["__PREVIEW_CANVAS__"].update(visible=True)
        window["__INFO__"].update(visible=False)
        window["__FRAME_SLIDER__"].update(visible=False)
        window["__GENERAL_PLAY_CONTAINER__"].update(visible=False)
        window["__TIMELINE_CONTAINER__"].update(visible=False)
        window["__LUTS_CONTAINER__"].update(visible=False)
    else:
        use_clahe = values.get("__USE_CLAHE__", False)
        window["__CLAHE_CLIP__"].update(disabled=not use_clahe)
        window["__CLAHE_CLIP_TEXT__"].update(text_color='white' if use_clahe else 'gray')
        
        use_saturation = values.get("__USE_SATURATION__", False)
        window["__SATURATION__"].update(disabled=not use_saturation)
        window["__SATURATION_TEXT__"].update(text_color='white' if use_saturation else 'gray')

        use_brightness = values.get("__USE_BRIGHTNESS__", False)
        window["__BRIGHTNESS__"].update(disabled=not use_brightness)
        window["__BRIGHTNESS_TEXT__"].update(text_color='white' if use_brightness else 'gray')
        window["__CONTRAST__"].update(disabled=not use_brightness)
        window["__CONTRAST_TEXT__"].update(text_color='white' if use_brightness else 'gray')
        
        if values.get("__SHOW_PREVIEW__", False):
            show_test_frame(window, values)
        else:
            window["__PREVIEW_FRAME__"].update(visible=False)
            window["__PREVIEW_CANVAS__"].update(visible=False)
            window["__INFO__"].update(visible=True)
            window["__FRAME_SLIDER__"].update(visible=False)
            window["__GENERAL_PLAY_CONTAINER__"].update(visible=False)
            window["__TIMELINE_CONTAINER__"].update(visible=False)
            window["__LUTS_CONTAINER__"].update(visible=False)

file_generator = None
current_task = None
analyze_video_generator = None
process_video_generator = None
process_start_time = None
preview_state = {"show": False}

current_project_name = None

last_ui_update_time = 0
last_frame_time = 0

last_slider_time = 0
pending_slider_update = False

file_markers = {}
undo_stack = []
redo_stack = []
last_undo_pushed_markers = None
last_kbd_undo_time = 0

video_markers = []
selected_marker_idx = None
is_dragging_marker = False
is_dragging_playhead = False

gen_play = False
gen_rew = False
play_speed = 1
restore_play_state = None

marker_action_active = None

current_frame = 1
current_frame_count = 1
current_fps = 30

scrubbing_cap = None
playback_frame_number = None
current_temp_filepath = None
current_output_filepath = None

def check_and_save_current_project(window, current_project_name, file_markers):
    current_files = window["__INPUT_FILE_LIST__"].get_list_values()
    if current_project_name:
        os.makedirs("Projects", exist_ok=True)
        project_path = os.path.join("Projects", f"{current_project_name}.cfg")
        data_to_save = {
            "project_name": current_project_name,
            "files": current_files,
            "markers": {f: file_markers[f] for f in current_files if f in file_markers and file_markers[f]},
            "ops_order": get_action_order(window),
            "applied_luts": window["__APPLIED_LUT_LIST__"].get_list_values()
        }
        try:
            with open(project_path, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f)
            with open("list.cfg", "w", encoding="utf-8") as f:
                json.dump(data_to_save, f)
        except:
            pass
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
                    "ops_order": get_action_order(window),
                    "applied_luts": window["__APPLIED_LUT_LIST__"].get_list_values()
                }
                try:
                    with open(project_path, "w", encoding="utf-8") as f:
                        json.dump(data_to_save, f)
                    with open("list.cfg", "w", encoding="utf-8") as f:
                        json.dump(data_to_save, f)
                except:
                    pass
                return True
            else:
                return False
        elif choice == "No":
            return True
        else:
            return False
    return True

if __name__ == "__main__":

    if os.path.exists("list.cfg"):
        try:
            with open("list.cfg", "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content.startswith("{"):
                    data = json.loads(content)
                    current_project_name = data.get("project_name")
                    saved_files = data.get("files", [])
                    file_markers.update(data.get("markers", {}))
                    ops_order = data.get("ops_order", DEFAULT_OPS)
                    window["__OPS_LIST__"].update(values=ops_order)
                    update_action_labels(window)
                    saved_applied_luts = [l for l in data.get("applied_luts", []) if os.path.exists(os.path.join(LUTS_DIR, l))]
                    window["__APPLIED_LUT_LIST__"].update(values=saved_applied_luts)
                    window["__LUT_LIST__"].update(values=get_available_luts_list(saved_applied_luts))
                else:
                    saved_files = [line.strip() for line in content.split('\n') if line.strip()]
                    
            valid_saved_files = [f for f in saved_files if valid_file(f)]
            window["__INPUT_FILE_LIST__"].update(valid_saved_files)
            if valid_saved_files:
                window["__OUTPUT_FOLDER__"].update(os.path.dirname(valid_saved_files[0]))
        except Exception as e:
            print(f"Could not load list.cfg: {e}")
            
    initial_filepath = get_selected_filepath(window)
    if initial_filepath:
        video_markers = file_markers.setdefault(initial_filepath, [])
        undo_stack.clear()
        redo_stack.clear()
        update_undo_redo_buttons(window)
        
    display_name = current_project_name if current_project_name else "[empty]"
    window["__MEDIA_FRAME__"].update(value=f"Media & Export [{display_name}]")

    while True:
        
        event, values = window.read(timeout=30)

        # --- GESTIONNAIRE D'ÉTATS D'IMAGES (EFFET CLIC) ---
        if event and type(event) == str:
            if event.endswith("_PRESS"):
                base_key = event.replace("_PRESS", "")
                if base_key in graphical_buttons:
                    click_img = os.path.join("assets", f"{base_key.strip('_').lower()}_click.png")
                    if os.path.exists(click_img):
                        window[base_key].update(image_filename=click_img)
            elif event.endswith("_RELEASE"):
                base_key = event.replace("_RELEASE", "")
                if base_key in graphical_buttons:
                    normal_img = os.path.join("assets", f"{base_key.strip('_').lower()}.png")
                    if os.path.exists(normal_img):
                        window[base_key].update(image_filename=normal_img)

        if event in ("z:26", "Z:52", "y:29", "Y:28") and (values.get("Control_L") or values.get("Control_R")):
            if event.lower() == "z":
                event = "__UNDO__"
            else:
                event = "__REDO__"

        if event == sg.WIN_CLOSED:
            if scrubbing_cap is not None:
                scrubbing_cap.release()
                
            try:
                current_files = window["__INPUT_FILE_LIST__"].get_list_values()
                data_to_save = {
                    "project_name": current_project_name,
                    "files": current_files,
                    "markers": {f: file_markers[f] for f in current_files if f in file_markers and file_markers[f]},
                    "ops_order": get_action_order(window),
                    "applied_luts": window["__APPLIED_LUT_LIST__"].get_list_values()
                }
                with open("list.cfg", "w", encoding="utf-8") as f:
                    json.dump(data_to_save, f)
            except Exception as e:
                print(f"Could not save list.cfg: {e}")
            
            if current_temp_filepath and os.path.exists(current_temp_filepath):
                try: os.remove(current_temp_filepath)
                except: pass
                
            break

        elif event == "__TIMELINE_RESIZE__":
            if not is_processing and current_frame_count > 0 and window["__TIMELINE_CONTAINER__"].visible:
                draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count)

        if values:
            preview_state["show"] = values.get("__SHOW_PREVIEW__", False)
            
        is_processing = file_generator is not None or analyze_video_generator is not None or process_video_generator is not None

        if event == "__SHOW_PREVIEW__":
            current_filepath = get_selected_filepath(window)
            if current_filepath:
                video_markers = file_markers.setdefault(current_filepath, [])
            else:
                video_markers = []
            undo_stack.clear()
            redo_stack.clear()
            update_undo_redo_buttons(window)
                
            if not is_processing:
                if values["__SHOW_PREVIEW__"]:
                    # Pass None to show_test_frame so it calculates the default frame
                    current_frame, current_frame_count, current_fps = show_test_frame(window, values, None)
                    update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                    draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count)
                    window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                else:
                    window["__PREVIEW_FRAME__"].update(visible=False)
                    window["__PREVIEW_CANVAS__"].update(visible=False)
                    window["__INFO__"].update(visible=True)
                    window["__FRAME_SLIDER__"].update(visible=False)
                    window["__GENERAL_PLAY_CONTAINER__"].update(visible=False)
                    window["__TIMELINE_CONTAINER__"].update(visible=False)
                    window["__LUTS_CONTAINER__"].update(visible=False)
                    window["__STATUS__"].update("")
            else:
                if values["__SHOW_PREVIEW__"]:
                    window["__INFO__"].update(visible=False)
                else:
                    window["__PREVIEW_FRAME__"].update(visible=False)
                    window["__PREVIEW_CANVAS__"].update(visible=False)
                    window["__INFO__"].update(visible=True)
                    window["__LUTS_CONTAINER__"].update(visible=False)
                    
        elif event == "__INPUT_FILE_LIST__":
            current_filepath = get_selected_filepath(window)
            if current_filepath:
                video_markers = file_markers.setdefault(current_filepath, [])
            else:
                video_markers = []
                
            undo_stack.clear()
            redo_stack.clear()
            update_undo_redo_buttons(window)
            restore_play_state = None

            selected_marker_idx = None
            gen_play = gen_rew = False
            play_speed = 1
            marker_action_active = None
            update_general_play_buttons(window, gen_play, gen_rew, play_speed)
            update_marker_buttons(window, selected_marker_idx)
            
            if scrubbing_cap is not None:
                scrubbing_cap.release()
                scrubbing_cap = None
            playback_frame_number = None

            window["__SHOW_PREVIEW__"].update(True)
            preview_state["show"] = True

            if not is_processing:
                # Pass None to show_test_frame so it calculates the default frame
                current_frame, current_frame_count, current_fps = show_test_frame(window, values, None)
                update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count)
                window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")

        elif event == "__ADD_LUT__":
            import tkinter as tk
            from tkinter import filedialog
            _tk_root = tk.Tk()
            _tk_root.withdraw()
            _tk_root.attributes('-topmost', True)
            filepaths = filedialog.askopenfilenames(
                title="Select LUT files",
                filetypes=[("LUT Files", "*.cube"), ("All Files", "*.*")],
                parent=_tk_root
            )
            _tk_root.destroy()

            if filepaths:
                added_count = 0
                for filepath in filepaths:
                    if filepath and os.path.exists(filepath):
                        try:
                            filename = os.path.basename(filepath)
                            dest_path = os.path.join(LUTS_DIR, filename)
                            if os.path.exists(dest_path):
                                choice = sg.popup_yes_no_cancel(
                                    f"'{filename}' already exists in LUTs folder.\n\nOverwrite, or rename (add .1, .2, ... suffix)?",
                                    title="LUT already exists",
                                    yes_text="Overwrite",
                                    no_text="Rename",
                                    button_color=('#ffffff', '#007acc')
                                )
                                if choice == "Overwrite":
                                    shutil.copy2(filepath, dest_path)
                                    added_count += 1
                                elif choice == "Rename":
                                    base, ext = os.path.splitext(filename)
                                    counter = 1
                                    while os.path.exists(os.path.join(LUTS_DIR, f"{base}.{counter}{ext}")):
                                        counter += 1
                                    new_name = f"{base}.{counter}{ext}"
                                    shutil.copy2(filepath, os.path.join(LUTS_DIR, new_name))
                                    added_count += 1
                                # else: Cancel → skip this file
                            else:
                                shutil.copy2(filepath, dest_path)
                                added_count += 1
                        except Exception as e:
                            print(f"Error adding LUT {filepath}: {e}")

                if added_count > 0:
                    applied = window["__APPLIED_LUT_LIST__"].get_list_values()
                    window["__LUT_LIST__"].update(values=get_available_luts_list(applied))
                    window["__STATUS__"].update(f"Added {added_count} LUT(s)")

        elif event == "__LUT_LIST__":
            selected_luts = values["__LUT_LIST__"]
            # Toggle: if user clicked the already-selected item, deselect it
            if hasattr(window["__LUT_LIST__"].Widget, '_last_lut_selection'):
                prev = window["__LUT_LIST__"].Widget._last_lut_selection
                if selected_luts == prev:
                    window["__LUT_LIST__"].update(set_to_index=[])
                    selected_luts = []
            window["__LUT_LIST__"].Widget._last_lut_selection = list(selected_luts)

            has_selection = len(selected_luts) > 0
            window["__LUT_ADD_APPLIED__"].update(disabled=not has_selection)
            window["__LUT_ONLY_BTN__"].update(disabled=not (has_selection or window["__APPLIED_LUT_LIST__"].get_list_values()))
            if not has_selection and not window["__APPLIED_LUT_LIST__"].get_list_values():
                window["__LUT_ONLY_BTN__"].update(button_color=('#ffffff', '#007acc'))
            update_action_labels(window)
            if not is_processing and values.get("__SHOW_PREVIEW__", False):
                show_test_frame(window, values, current_frame)

        elif event == "__APPLIED_LUT_LIST__":
            # Toggle deselect on applied list
            selected_applied = values["__APPLIED_LUT_LIST__"]
            if hasattr(window["__APPLIED_LUT_LIST__"].Widget, '_last_applied_selection'):
                prev = window["__APPLIED_LUT_LIST__"].Widget._last_applied_selection
                if selected_applied == prev:
                    window["__APPLIED_LUT_LIST__"].update(set_to_index=[])
                    selected_applied = []
            window["__APPLIED_LUT_LIST__"].Widget._last_applied_selection = list(selected_applied)
            applied = window["__APPLIED_LUT_LIST__"].get_list_values()
            has_selection_applied = len(selected_applied) > 0
            window["__LUT_REM_APPLIED__"].update(disabled=not has_selection_applied)
            if selected_applied:
                idx = applied.index(selected_applied[0])
                window["__APPLIED_LUT_UP__"].update(disabled=(idx == 0))
                window["__APPLIED_LUT_DOWN__"].update(disabled=(idx == len(applied) - 1))
            else:
                window["__APPLIED_LUT_UP__"].update(disabled=True)
                window["__APPLIED_LUT_DOWN__"].update(disabled=True)

        elif event == "__LUT_ADD_APPLIED__":
            # Move selected LUT from available list to applied list
            selected_luts = window["__LUT_LIST__"].get()
            if selected_luts:
                name = selected_luts[0]
                applied = list(window["__APPLIED_LUT_LIST__"].get_list_values())
                if name not in applied:
                    applied.append(name)
                    window["__APPLIED_LUT_LIST__"].update(values=applied)
                    window["__LUT_LIST__"].update(values=get_available_luts_list(applied), set_to_index=[])
                    window["__LUT_LIST__"].Widget._last_lut_selection = []
                    window["__LUT_ADD_APPLIED__"].update(disabled=True)
                    window["__APPLIED_LUT_UP__"].update(disabled=True)
                    window["__APPLIED_LUT_DOWN__"].update(disabled=True)
                    update_action_labels(window)
                    if not is_processing and values.get("__SHOW_PREVIEW__", False):
                        show_test_frame(window, values, current_frame)

        elif event == "__LUT_REM_APPLIED__":
            # Move selected LUT from applied list back to available list
            selected_applied = window["__APPLIED_LUT_LIST__"].get()
            if selected_applied:
                name = selected_applied[0]
                applied = list(window["__APPLIED_LUT_LIST__"].get_list_values())
                applied.remove(name)
                window["__APPLIED_LUT_LIST__"].update(values=applied, set_to_index=[])
                window["__APPLIED_LUT_LIST__"].Widget._last_applied_selection = []
                window["__LUT_LIST__"].update(values=get_available_luts_list(applied))
                window["__LUT_REM_APPLIED__"].update(disabled=True)
                window["__APPLIED_LUT_UP__"].update(disabled=True)
                window["__APPLIED_LUT_DOWN__"].update(disabled=True)
                update_action_labels(window)
                if not is_processing and values.get("__SHOW_PREVIEW__", False):
                    show_test_frame(window, values, current_frame)
                if not is_processing and values.get("__SHOW_PREVIEW__", False):
                    show_test_frame(window, values, current_frame)

        elif event == "__APPLIED_LUT_UP__":
            selected_applied = window["__APPLIED_LUT_LIST__"].get()
            if selected_applied:
                applied = list(window["__APPLIED_LUT_LIST__"].get_list_values())
                idx = applied.index(selected_applied[0])
                if idx > 0:
                    applied[idx - 1], applied[idx] = applied[idx], applied[idx - 1]
                    window["__APPLIED_LUT_LIST__"].update(values=applied, set_to_index=idx - 1)
                    window["__APPLIED_LUT_UP__"].update(disabled=(idx - 1 == 0))
                    window["__APPLIED_LUT_DOWN__"].update(disabled=(idx - 1 == len(applied) - 1))
                    if not is_processing and values.get("__SHOW_PREVIEW__", False):
                        show_test_frame(window, values, current_frame)

        elif event == "__APPLIED_LUT_DOWN__":
            selected_applied = window["__APPLIED_LUT_LIST__"].get()
            if selected_applied:
                applied = list(window["__APPLIED_LUT_LIST__"].get_list_values())
                idx = applied.index(selected_applied[0])
                if idx < len(applied) - 1:
                    applied[idx + 1], applied[idx] = applied[idx], applied[idx + 1]
                    window["__APPLIED_LUT_LIST__"].update(values=applied, set_to_index=idx + 1)
                    window["__APPLIED_LUT_UP__"].update(disabled=(idx + 1 == 0))
                    window["__APPLIED_LUT_DOWN__"].update(disabled=(idx + 1 == len(applied) - 1))
                    if not is_processing and values.get("__SHOW_PREVIEW__", False):
                        show_test_frame(window, values, current_frame)

        elif event == "__LUT_ONLY_BTN__":
            if not is_processing:
                current_color = window["__LUT_ONLY_BTN__"].ButtonColor[1]
                # Toggle color between default blue and active green
                new_color = '#28a745' if current_color != '#28a745' else '#007acc'
                window["__LUT_ONLY_BTN__"].update(button_color=('#ffffff', new_color))
                
                if values.get("__SHOW_PREVIEW__", False):
                    show_test_frame(window, values, current_frame)

        elif event == "__OPS_LIST__":
            selected_ops = values["__OPS_LIST__"]
            # Toggle: if user clicked the already-selected item, deselect it
            if hasattr(window["__OPS_LIST__"].Widget, '_last_op_selection'):
                prev = window["__OPS_LIST__"].Widget._last_op_selection
                if selected_ops == prev:
                    window["__OPS_LIST__"].update(set_to_index=[])
                    selected_ops = []
            window["__OPS_LIST__"].Widget._last_op_selection = list(selected_ops)

            if selected_ops:
                idx = window["__OPS_LIST__"].get_list_values().index(selected_ops[0])
                window["__OP_UP__"].update(disabled=(idx == 0))
                window["__OP_DOWN__"].update(disabled=(idx == len(window["__OPS_LIST__"].get_list_values()) - 1))
            else:
                window["__OP_UP__"].update(disabled=True)
                window["__OP_DOWN__"].update(disabled=True)

        elif event == "__OP_UP__":
            selected_ops = window["__OPS_LIST__"].get()
            if selected_ops:
                ops_list = window["__OPS_LIST__"].get_list_values()
                idx = ops_list.index(selected_ops[0])
                if idx > 0:
                    ops_list[idx], ops_list[idx - 1] = ops_list[idx - 1], ops_list[idx]
                    window["__OPS_LIST__"].update(values=ops_list, set_to_index=[idx - 1])
                    window["__OPS_LIST__"].Widget._last_op_selection = [selected_ops[0]]
                    window["__OP_UP__"].update(disabled=(idx - 1 == 0))
                    window["__OP_DOWN__"].update(disabled=(idx - 1 == len(ops_list) - 1))
                    if not is_processing and values.get("__SHOW_PREVIEW__", False):
                        show_test_frame(window, values, current_frame)

        elif event == "__OP_DOWN__":
            selected_ops = window["__OPS_LIST__"].get()
            if selected_ops:
                ops_list = window["__OPS_LIST__"].get_list_values()
                idx = ops_list.index(selected_ops[0])
                if idx < len(ops_list) - 1:
                    ops_list[idx], ops_list[idx + 1] = ops_list[idx + 1], ops_list[idx]
                    window["__OPS_LIST__"].update(values=ops_list, set_to_index=[idx + 1])
                    window["__OPS_LIST__"].Widget._last_op_selection = [selected_ops[0]]
                    window["__OP_UP__"].update(disabled=(idx + 1 == 0))
                    window["__OP_DOWN__"].update(disabled=(idx + 1 == len(ops_list) - 1))
                    if not is_processing and values.get("__SHOW_PREVIEW__", False):
                        show_test_frame(window, values, current_frame)

        # --- LIENS CREDITS ---
        elif event == "__ORIGINAL_LINK__":
            if not is_processing:
                webbrowser.open("https://bornfree.github.io/dive-color-corrector/")           

        elif event == "__ALGO_LINK__":
            if not is_processing:
                webbrowser.open("https://github.com/nikolajbech/underwater-image-color-correction")

        # --- MENU CONTEXTUEL : OUVRIR FICHIER / EXPLORER ---
        elif event == "Open File":
            selected = window["__INPUT_FILE_LIST__"].get()
            if selected and os.path.isfile(selected[0]):
                try:
                    os.startfile(selected[0])
                except Exception as e:
                    window["__STATUS__"].update(f"Cannot open file: {e}")

        elif event == "Open in Explorer":
            selected = window["__INPUT_FILE_LIST__"].get()
            if selected and os.path.isfile(selected[0]):
                try:
                    filepath = os.path.normpath(selected[0])
                    subprocess.Popen(f'explorer /select,"{filepath}"')
                except Exception as e:
                    window["__STATUS__"].update(f"Cannot open explorer: {e}")

        # --- MENU CONTEXTUEL DELETE (
        elif event in ("Delete", "Remove from list"):
            if not is_processing:
                selected = window["__INPUT_FILE_LIST__"].get()
                if selected:
                    file_to_remove = selected[0]
                    current_files = window["__INPUT_FILE_LIST__"].get_list_values()
                    if file_to_remove in current_files:
                        current_files.remove(file_to_remove)
                        window["__INPUT_FILE_LIST__"].update(values=current_files)
                        
                        if file_to_remove in file_markers:
                            del file_markers[file_to_remove]
                        
                        if len(current_files) == 0:
                            if values.get("__SHOW_PREVIEW__", False) or window["__PREVIEW_CANVAS__"].visible:
                                window["__SHOW_PREVIEW__"].update(False)
                                preview_state["show"] = False
                                window["__PREVIEW_CANVAS__"].update(visible=False)
                                window["__INFO__"].update(visible=True)
                                window["__FRAME_SLIDER__"].update(visible=False)
                                window["__GENERAL_PLAY_CONTAINER__"].update(visible=False)
                                window["__TIMELINE_CONTAINER__"].update(visible=False)
                                window["__LUTS_CONTAINER__"].update(visible=False)
                            video_markers = []
                            selected_marker_idx = None
                            update_marker_buttons(window, selected_marker_idx)
                            window["__STATUS__"].update("")
                        else:
                            window["__INPUT_FILE_LIST__"].update(set_to_index=0)
                            window.write_event_value("__INPUT_FILE_LIST__", None)

        # --- FULL PREVIEW ACTION ---
        elif event == "__FULL_PREVIEW___PRESS":
            if not is_processing:
                restore_play_state = (gen_play, gen_rew, play_speed)
                gen_play = gen_rew = False
                play_speed = 1
                update_general_play_buttons(window, False, False, 1)
                
                current_filepath = get_selected_filepath(window)
                if current_filepath and valid_file(current_filepath):
                    use_clahe = values.get("__USE_CLAHE__", False)
                    clahe_clip = values.get("__CLAHE_CLIP__", 2.0)
                    use_saturation = values.get("__USE_SATURATION__", False)
                    saturation_val = values.get("__SATURATION__", 1.0)
                    use_brightness = values.get("__USE_BRIGHTNESS__", False)
                    brightness_val = values.get("__BRIGHTNESS__", 0)
                    contrast_val = values.get("__CONTRAST__", 1.0)
                    red_str = values.get("__RED_STRENGTH__", 60)
                    hue_shift = values.get("__HUE_SHIFT__", 120)
                    
                    luts = get_selected_lut_arrays(window)
                    lut_only = False
                    ops_order = get_active_action_order(window)
                    
                    # Use the slider value (the frame actually shown) rather than
                    # the global current_frame, which may be stale during playback.
                    try:
                        target_frame = int(window["__FRAME_SLIDER__"].get())
                    except Exception:
                        target_frame = current_frame

                    frame = None
                    extension = current_filepath[current_filepath.rfind("."):].lower()
                    if extension in VIDEO_TYPES:
                        cap = cv2.VideoCapture(current_filepath)
                        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, target_frame - 1))
                        ret, f = cap.read()
                        cap.release()
                        if ret and f is not None:
                            frame = f
                    elif extension in IMAGE_TYPES:
                        f = cv2.imread(current_filepath)
                        if f is not None:
                            frame = f

                    if frame is not None:
                        rgb_mat = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        corrected_mat = correct(rgb_mat, use_clahe, red_str, hue_shift, clahe_clip, use_saturation, saturation_val, use_brightness, brightness_val, contrast_val, luts, lut_only, ops_order)
                        # corrected_mat is RGB, draw_preview_on_canvas expects BGR
                        bgr_mat = cv2.cvtColor(corrected_mat, cv2.COLOR_RGB2BGR)
                        draw_preview_on_canvas(window, bgr_mat)
                        # Keep slider in sync with the frame we just processed
                        window["__FRAME_SLIDER__"].update(value=target_frame)
                        current_frame = target_frame

        elif event == "__FULL_PREVIEW___RELEASE":
            if not is_processing:
                if restore_play_state:
                    gen_play, gen_rew, play_speed = restore_play_state
                    restore_play_state = None
                    update_general_play_buttons(window, gen_play, gen_rew, play_speed)

                # Read the frame the slider points to — not the stale global, to
                # avoid a one-frame jump after releasing Full Preview.
                try:
                    rel_frame = int(window["__FRAME_SLIDER__"].get())
                except Exception:
                    rel_frame = current_frame

                is_playing = gen_play or gen_rew
                if not is_playing:
                    show_test_frame(window, values, rel_frame)
                    window["__STATUS__"].update(f"Frame {rel_frame} / {current_frame_count}")
                else:
                    if scrubbing_cap is None:
                        current_filepath = get_selected_filepath(window)
                        if current_filepath and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                            scrubbing_cap = cv2.VideoCapture(current_filepath)
                    if scrubbing_cap is not None:
                        update_raw_preview(window, scrubbing_cap, current_frame)
                    window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")

        # --- COMMANDES DE LECTURE GLOBALE ---
        elif event in ("__GEN_PLAY__", "__GEN_REW__", "__GEN_STOP__"):
            selected_marker_idx = None
            update_marker_buttons(window, selected_marker_idx)
            
            was_playing = gen_play or gen_rew

            if event == "__GEN_PLAY__":
                if gen_play:
                    play_speed = min(64, play_speed * 2)
                else:
                    gen_play = True
                    gen_rew = False
                    play_speed = 1
            elif event == "__GEN_REW__":
                if gen_rew:
                    play_speed = min(64, play_speed * 2)
                else:
                    gen_rew = True
                    gen_play = False
                    play_speed = 1
            elif event == "__GEN_STOP__":
                gen_play = False
                gen_rew = False
                play_speed = 1

            update_general_play_buttons(window, gen_play, gen_rew, play_speed)
            
            is_playing = gen_play or gen_rew
            if was_playing and not is_playing and not is_processing:
                show_test_frame(window, values, current_frame)

        # --- GESTION DES RACCOURCIS CLAVIER MARKERS & LECTURE ---
        elif event in ("__KBD_LEFT", "__KBD_RIGHT", "__KBD_CTRL_LEFT", "__KBD_CTRL_RIGHT"):
            if not is_processing and current_frame_count > 0:
                step = 0
                if event == "__KBD_LEFT": step = -1
                elif event == "__KBD_RIGHT": step = 1
                elif event == "__KBD_CTRL_LEFT": step = -FAST_STEP
                elif event == "__KBD_CTRL_RIGHT": step = FAST_STEP

                if selected_marker_idx is not None:
                    now = time.time()
                    if now - last_kbd_undo_time > 0.5:
                        push_undo_state(video_markers)
                    last_kbd_undo_time = now
                    
                    min_f = 1
                    max_f = current_frame_count
                    if selected_marker_idx > 0:
                        min_f = video_markers[selected_marker_idx - 1] + 1
                    if selected_marker_idx < len(video_markers) - 1:
                        max_f = video_markers[selected_marker_idx + 1] - 1
                        
                    if selected_marker_idx % 2 == 0 and selected_marker_idx + 2 < len(video_markers):
                        next_start = video_markers[selected_marker_idx + 2]
                        max_f = min(max_f, next_start - 50)

                    new_m = max(min_f, min(video_markers[selected_marker_idx] + step, max_f))
                    if new_m != video_markers[selected_marker_idx]:
                        video_markers[selected_marker_idx] = new_m
                        current_frame = new_m
                        window["__FRAME_SLIDER__"].update(value=current_frame)
                        
                        if scrubbing_cap is None:
                            current_filepath = get_selected_filepath(window)
                            if current_filepath and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                                scrubbing_cap = cv2.VideoCapture(current_filepath)
                        
                        if scrubbing_cap is not None:
                            update_raw_preview(window, scrubbing_cap, current_frame)
                            
                        draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count, only_playhead=True)
                        window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                        
                        last_slider_time = time.time()
                        pending_slider_update = True
                else:
                    new_frame = current_frame + step
                    current_frame = max(1, min(new_frame, current_frame_count))
                    
                    window["__FRAME_SLIDER__"].update(value=current_frame)
                    update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                    draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count, only_playhead=True)

                    if scrubbing_cap is None:
                        current_filepath = get_selected_filepath(window)
                        if current_filepath and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                            scrubbing_cap = cv2.VideoCapture(current_filepath)
                    if scrubbing_cap is not None:
                        update_raw_preview(window, scrubbing_cap, current_frame)

                    window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                    
                    last_slider_time = time.time()
                    pending_slider_update = True

        # --- GESTION DES BOUTONS MOUSE POUR MARKERS & LECTURE (<<, <, >, >>) ---
        elif event in ("__MARKER_FAST_REW___PRESS", "__MARKER_REW___PRESS", "__MARKER_FWD___PRESS", "__MARKER_FAST_FWD___PRESS"):
            if not is_processing and current_frame_count > 0:
                marker_action_active = event.replace("_PRESS", "")
                step = 0
                if marker_action_active == "__MARKER_FAST_REW__": step = -FAST_STEP
                elif marker_action_active == "__MARKER_REW__": step = -1
                elif marker_action_active == "__MARKER_FWD__": step = 1
                elif marker_action_active == "__MARKER_FAST_FWD__": step = FAST_STEP

                if selected_marker_idx is not None:
                    last_undo_pushed_markers = list(video_markers)
                    
                    min_f = 1
                    max_f = current_frame_count
                    if selected_marker_idx > 0:
                        min_f = video_markers[selected_marker_idx - 1] + 1
                    if selected_marker_idx < len(video_markers) - 1:
                        max_f = video_markers[selected_marker_idx + 1] - 1
                        
                    if selected_marker_idx % 2 == 0 and selected_marker_idx + 2 < len(video_markers):
                        next_start = video_markers[selected_marker_idx + 2]
                        max_f = min(max_f, next_start - 50)

                    new_m = max(min_f, min(video_markers[selected_marker_idx] + step, max_f))
                    if new_m != video_markers[selected_marker_idx]:
                        video_markers[selected_marker_idx] = new_m
                        current_frame = new_m
                        window["__FRAME_SLIDER__"].update(value=current_frame)
                        
                        if scrubbing_cap is None:
                            current_filepath = get_selected_filepath(window)
                            if current_filepath and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                                scrubbing_cap = cv2.VideoCapture(current_filepath)
                        
                        if scrubbing_cap is not None:
                            update_raw_preview(window, scrubbing_cap, current_frame)
                            
                        draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count, only_playhead=True)
                        window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                        
                        last_slider_time = time.time()
                        pending_slider_update = True
                else:
                    new_frame = current_frame + step
                    current_frame = max(1, min(new_frame, current_frame_count))
                    
                    window["__FRAME_SLIDER__"].update(value=current_frame)
                    update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                    draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count, only_playhead=True)

                    if scrubbing_cap is None:
                        current_filepath = get_selected_filepath(window)
                        if current_filepath and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                            scrubbing_cap = cv2.VideoCapture(current_filepath)
                    if scrubbing_cap is not None:
                        update_raw_preview(window, scrubbing_cap, current_frame)

                    window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                    
                    last_slider_time = time.time()
                    pending_slider_update = True

        elif event in ("__MARKER_FAST_REW___RELEASE", "__MARKER_REW___RELEASE", "__MARKER_FWD___RELEASE", "__MARKER_FAST_FWD___RELEASE"):
            if marker_action_active is not None:
                if selected_marker_idx is not None:
                    if last_undo_pushed_markers and last_undo_pushed_markers != video_markers:
                        undo_stack.append(list(last_undo_pushed_markers))
                        redo_stack.clear()
                        update_undo_redo_buttons(window)
                    last_undo_pushed_markers = None
                
                marker_action_active = None
                last_slider_time = time.time() - 0.20 

        # --- LOGIQUE TIMELINE, SÉQUENCES & UNDO/REDO ---
        
        elif event == "__ADD_MARK__":
            if not is_processing:
                target_frame = current_frame
                
                can_add = True
                if target_frame > current_frame_count - int(3 * current_fps):
                    can_add = False
                    
                for i in range(0, len(video_markers), 2):
                    start_m = video_markers[i]
                    if target_frame <= start_m and (start_m - target_frame) < 50:
                        can_add = False
                        break

                if can_add and target_frame not in video_markers and not is_frame_inside_sequence(target_frame, video_markers):
                    push_undo_state(video_markers)
                    
                    video_markers.append(target_frame)
                    video_markers.sort()
                    
                    end_target = target_frame + int(5 * current_fps)
                    end_target = min(end_target, current_frame_count)
                    
                    for m in video_markers:
                        if m > target_frame and m < end_target:
                            end_target = m - 1
                            break
                    
                    if end_target > target_frame and end_target not in video_markers:
                        video_markers.append(end_target)
                        video_markers.sort()

                    selected_marker_idx = video_markers.index(end_target)
                    
                    current_frame = end_target
                    window["__FRAME_SLIDER__"].update(value=current_frame)
                    show_test_frame(window, values, current_frame)
                    
                    update_marker_buttons(window, selected_marker_idx)
                    update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                    draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count)
                    window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")

        elif event == "__DEL_MARK__":
            if not is_processing and selected_marker_idx is not None:
                if selected_marker_idx % 2 == 0:
                    push_undo_state(video_markers)
                    
                    if selected_marker_idx + 1 < len(video_markers):
                        video_markers.pop(selected_marker_idx + 1)
                    video_markers.pop(selected_marker_idx)

                    selected_marker_idx = None
                    update_marker_buttons(window, selected_marker_idx)
                    update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                    draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count)
                    window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")

        elif event == "__UNDO__":
            if not is_processing and len(undo_stack) > 0:
                redo_stack.append(list(video_markers))
                new_markers = undo_stack.pop()
                video_markers.clear()
                video_markers.extend(new_markers)
                selected_marker_idx = None
                update_undo_redo_buttons(window)
                update_marker_buttons(window, selected_marker_idx)
                update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count)
                window["__STATUS__"].update("Undo performed")

        elif event == "__REDO__":
            if not is_processing and len(redo_stack) > 0:
                undo_stack.append(list(video_markers))
                new_markers = redo_stack.pop()
                video_markers.clear()
                video_markers.extend(new_markers)
                selected_marker_idx = None
                update_undo_redo_buttons(window)
                update_marker_buttons(window, selected_marker_idx)
                update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count)
                window["__STATUS__"].update("Redo performed")

        elif event == "__TIMELINE___PRESS":
            if not is_processing and current_frame_count > 0:
                x, y = values["__TIMELINE__"]
                if x is not None:
                    min_dist = current_frame_count * 0.04
                    closest_idx = None
                    for i, m in enumerate(video_markers):
                        dist = abs(m - x)
                        if dist < min_dist:
                            min_dist = dist
                            closest_idx = i
                    
                    if closest_idx is not None:
                        if selected_marker_idx == closest_idx:
                            selected_marker_idx = None
                            is_dragging_marker = False
                        else:
                            selected_marker_idx = closest_idx
                            is_dragging_marker = True
                            push_undo_state(video_markers)
                            
                        if selected_marker_idx is not None:
                            current_frame = video_markers[selected_marker_idx]
                            window["__FRAME_SLIDER__"].update(value=current_frame)
                            show_test_frame(window, values, current_frame)
                            window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                    else:
                        selected_marker_idx = None
                        is_dragging_playhead = True
                        current_frame = max(1, min(int(x), current_frame_count))
                        window["__FRAME_SLIDER__"].update(value=current_frame)
                        show_test_frame(window, values, current_frame)
                        window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                    
                    update_marker_buttons(window, selected_marker_idx)
                    update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                    draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count)

        elif event == "__TIMELINE__":
            if not is_processing and current_frame_count > 0:
                x, y = values["__TIMELINE__"]
                if x is not None:
                    x = max(1, min(int(x), current_frame_count))
                    
                    if is_dragging_marker and selected_marker_idx is not None:
                        min_f = 1
                        max_f = current_frame_count
                        if selected_marker_idx > 0:
                            min_f = video_markers[selected_marker_idx - 1] + 1
                        if selected_marker_idx < len(video_markers) - 1:
                            max_f = video_markers[selected_marker_idx + 1] - 1
                        
                        if selected_marker_idx % 2 == 0 and selected_marker_idx + 2 < len(video_markers):
                            next_start = video_markers[selected_marker_idx + 2]
                            max_f = min(max_f, next_start - 50)
                            
                        x = max(min_f, min(x, max_f))
                        video_markers[selected_marker_idx] = x
                        current_frame = x
                        window["__FRAME_SLIDER__"].update(value=current_frame)
                        update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                        draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count)
                        window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                        
                        if scrubbing_cap is None:
                            current_filepath = get_selected_filepath(window)
                            if current_filepath and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                                scrubbing_cap = cv2.VideoCapture(current_filepath)
                        
                        if scrubbing_cap is not None:
                            update_raw_preview(window, scrubbing_cap, current_frame)
                            
                        last_slider_time = time.time()
                        pending_slider_update = True
                        
                    elif is_dragging_playhead:
                        current_frame = x
                        window["__FRAME_SLIDER__"].update(value=current_frame)
                        
                        if selected_marker_idx is not None and selected_marker_idx < len(video_markers):
                            video_markers[selected_marker_idx] = current_frame
                            
                        update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                        draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count, only_playhead=True)
                        window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                        
                        if scrubbing_cap is None:
                            current_filepath = get_selected_filepath(window)
                            if current_filepath and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                                scrubbing_cap = cv2.VideoCapture(current_filepath)
                        
                        if scrubbing_cap is not None:
                            update_raw_preview(window, scrubbing_cap, current_frame)
                            
                        last_slider_time = time.time()
                        pending_slider_update = True

        elif event == "__TIMELINE___RELEASE":
            is_dragging_marker = False
            is_dragging_playhead = False
            if selected_marker_idx is None:
                is_dragging_marker = False
            if scrubbing_cap is not None:
                scrubbing_cap.release()
                scrubbing_cap = None
            playback_frame_number = None

        elif event == "__SAVE_FRAME__":
            if not is_processing:
                current_filepath = get_selected_filepath(window)
                if current_filepath and valid_file(current_filepath) and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                    window["__STATUS__"].update("Saving frame...")
                    window.refresh()
                    
                    use_clahe = values.get("__USE_CLAHE__", False)
                    clahe_clip = values.get("__CLAHE_CLIP__", 2.0)
                    use_saturation = values.get("__USE_SATURATION__", False)
                    saturation_val = values.get("__SATURATION__", 1.0)
                    use_brightness = values.get("__USE_BRIGHTNESS__", False)
                    brightness_val = values.get("__BRIGHTNESS__", 0)
                    contrast_val = values.get("__CONTRAST__", 1.0)
                    red_str = values.get("__RED_STRENGTH__", 60)
                    hue_shift = values.get("__HUE_SHIFT__", 120)
                    
                    luts = get_selected_lut_arrays(window)
                    lut_only = window["__LUT_ONLY_BTN__"].ButtonColor[1] == '#28a745' if hasattr(window["__LUT_ONLY_BTN__"], 'ButtonColor') else False
                    ops_order = get_active_action_order(window)
                    
                    cap = cv2.VideoCapture(current_filepath)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, current_frame - 1))
                    ret, frame = cap.read()
                    cap.release()
                    
                    if ret and frame is not None:
                        rgb_mat = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        corrected_mat = correct(rgb_mat, use_clahe, red_str, hue_shift, clahe_clip, use_saturation, saturation_val, use_brightness, brightness_val, contrast_val, luts, lut_only, ops_order)
                        
                        base_name = os.path.basename(current_filepath)
                        name_without_ext, _ = os.path.splitext(base_name)
                        
                        prefix = values.get("__OUTPUT_PREFIX__", "corrected")
                        is_suffix = values.get("__IS_SUFFIX__", True)
                        out_folder = values.get("__OUTPUT_FOLDER__", "./")
                        
                        if is_suffix:
                            new_filename = f"{name_without_ext}_{prefix}_{current_frame}.jpg"
                        else:
                            new_filename = f"{prefix}_{name_without_ext}_{current_frame}.jpg"
                            
                        output_filepath = os.path.join(out_folder, new_filename)
                        
                        if os.path.exists(output_filepath):
                            choice = sg.popup_yes_no(f"The file '{new_filename}' already exists.\nDo you want to overwrite it?", title="File Exists")
                            if choice == "Yes":
                                try:
                                    os.remove(output_filepath)
                                except Exception as e:
                                    sg.popup_error(f"Could not delete file: {e}")
                                    window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                                    continue
                            else:
                                counter = 1
                                while True:
                                    if is_suffix:
                                        new_filename = f"{name_without_ext}_{prefix}_{current_frame}.{counter}.jpg"
                                    else:
                                        new_filename = f"{prefix}_{name_without_ext}_{current_frame}.{counter}.jpg"
                                    output_filepath = os.path.join(out_folder, new_filename)
                                    if not os.path.exists(output_filepath):
                                        break
                                    counter += 1
                                    
                        corrected_mat_bgr = cv2.cvtColor(corrected_mat, cv2.COLOR_RGB2BGR)
                        cv2.imwrite(output_filepath, corrected_mat_bgr)
                        window["__STATUS__"].update(f"Frame saved as {new_filename}")
                    else:
                        window["__STATUS__"].update("Error extracting frame to save.")


        elif event == sg.TIMEOUT_EVENT and not is_processing:
            if marker_action_active:
                step = 0
                if marker_action_active == "__MARKER_FAST_REW__": step = -FAST_STEP
                elif marker_action_active == "__MARKER_REW__": step = -1
                elif marker_action_active == "__MARKER_FWD__": step = 1
                elif marker_action_active == "__MARKER_FAST_FWD__": step = FAST_STEP

                if selected_marker_idx is not None:
                    min_f = 1
                    max_f = current_frame_count
                    if selected_marker_idx > 0:
                        min_f = video_markers[selected_marker_idx - 1] + 1
                    if selected_marker_idx < len(video_markers) - 1:
                        max_f = video_markers[selected_marker_idx + 1] - 1
                        
                    if selected_marker_idx % 2 == 0 and selected_marker_idx + 2 < len(video_markers):
                        next_start = video_markers[selected_marker_idx + 2]
                        max_f = min(max_f, next_start - 50)

                    new_m = max(min_f, min(video_markers[selected_marker_idx] + step, max_f))
                    if new_m != video_markers[selected_marker_idx]:
                        video_markers[selected_marker_idx] = new_m
                        current_frame = new_m
                        window["__FRAME_SLIDER__"].update(value=current_frame)
                        
                        if scrubbing_cap is None:
                            current_filepath = get_selected_filepath(window)
                            if current_filepath and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                                scrubbing_cap = cv2.VideoCapture(current_filepath)
                        
                        if scrubbing_cap is not None:
                            update_raw_preview(window, scrubbing_cap, current_frame)
                            
                        draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count, only_playhead=True)
                        window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                        
                        last_slider_time = time.time()
                        pending_slider_update = True
                else:
                    new_frame = current_frame + step
                    current_frame = max(1, min(new_frame, current_frame_count))
                    
                    window["__FRAME_SLIDER__"].update(value=current_frame)
                    update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                    draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count, only_playhead=True)

                    if scrubbing_cap is None:
                        current_filepath = get_selected_filepath(window)
                        if current_filepath and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                            scrubbing_cap = cv2.VideoCapture(current_filepath)
                    if scrubbing_cap is not None:
                        update_raw_preview(window, scrubbing_cap, current_frame)

                    window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                    
                    last_slider_time = time.time()
                    pending_slider_update = True

            elif gen_play or gen_rew:
                step = 0
                if gen_play: step = play_speed
                elif gen_rew: step = -play_speed

                new_frame = current_frame + step
                current_frame = max(1, min(new_frame, current_frame_count))

                stopped_at_bound = False
                if current_frame == current_frame_count and step > 0:
                    gen_play = False
                    play_speed = 1
                    stopped_at_bound = True
                elif current_frame == 1 and step < 0:
                    gen_rew = False
                    play_speed = 1
                    stopped_at_bound = True

                update_general_play_buttons(window, gen_play, gen_rew, play_speed)
                window["__FRAME_SLIDER__"].update(value=current_frame)
                update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count, only_playhead=True)

                if stopped_at_bound:
                    show_test_frame(window, values, current_frame)
                    window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                else:
                    # Optimized playback: read frames sequentially without seeking per frame.
                    # This avoids the slow cap.set(cv2.CAP_PROP_POS_FRAMES, ...) + cap.read()
                    # cycle which forces OpenCV to re-decode from the nearest keyframe.
                    # Instead, we keep scrubbing_cap open and use sequential cap.read() calls,
                    # seeking only when the frame position changes (e.g., user scrub or speed change).
                    if scrubbing_cap is None:
                        current_filepath = get_selected_filepath(window)
                        if current_filepath and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                            scrubbing_cap = cv2.VideoCapture(current_filepath)
                            # Start one frame behind so the seek logic triggers and
                            # positions the capture at (current_frame - 1) before reading.
                            playback_frame_number = max(0, current_frame - 1)
                    
                    if scrubbing_cap is not None:
                        # Seek only when we need to jump to a different frame position.
                        # Once seeking is done, subsequent reads are sequential (fast).
                        if playback_frame_number != current_frame:
                            scrubbing_cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame - 1)
                            playback_frame_number = current_frame
                        
                        # Read the next frame sequentially (no seek)
                        ret, frame = scrubbing_cap.read()
                        if ret and frame is not None:
                            # During playback, display raw frame (no image processing)
                            # for maximum speed. Resize to 1080 width for display.
                            draw_preview_on_canvas(window, frame)
                            playback_frame_number += 1
                        else:
                            # End of video reached during playback
                            gen_play = False
                            gen_rew = False
                            play_speed = 1
                            playback_frame_number = None
                            update_general_play_buttons(window, gen_play, gen_rew, play_speed)
                        
                        window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                    else:
                        window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                
        # --- FIN TIMELINE ---

        elif event in ("__USE_CLAHE__", "__USE_SATURATION__", "__USE_BRIGHTNESS__"):
            pending_slider_update = False 
            
            if not is_processing:
                if event == "__USE_CLAHE__":
                    use_clahe = values.get("__USE_CLAHE__", False)
                    window["__CLAHE_CLIP__"].update(disabled=not use_clahe)
                    window["__CLAHE_CLIP_TEXT__"].update(text_color='white' if use_clahe else 'gray')
                elif event == "__USE_SATURATION__":
                    use_saturation = values.get("__USE_SATURATION__", False)
                    window["__SATURATION__"].update(disabled=not use_saturation)
                    window["__SATURATION_TEXT__"].update(text_color='white' if use_saturation else 'gray')
                elif event == "__USE_BRIGHTNESS__":
                    use_brightness = values.get("__USE_BRIGHTNESS__", False)
                    window["__BRIGHTNESS__"].update(disabled=not use_brightness)
                    window["__BRIGHTNESS_TEXT__"].update(text_color='white' if use_brightness else 'gray')
                    window["__CONTRAST__"].update(disabled=not use_brightness)
                    window["__CONTRAST_TEXT__"].update(text_color='white' if use_brightness else 'gray')

                update_action_labels(window)
                
                if window["__PREVIEW_CANVAS__"].visible:
                    window["__STATUS__"].update("Applying settings...")
                    show_test_frame(window, values, current_frame)
                    update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                    window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                            
        elif event in ("__RED_STRENGTH__", "__HUE_SHIFT__", "__CLAHE_CLIP__", "__SATURATION__", "__BRIGHTNESS__", "__CONTRAST__"):
            if not is_processing:
                last_slider_time = time.time()
                pending_slider_update = True
                window["__STATUS__"].update("Adjusting parameters...")
                
        elif event == "__FRAME_SLIDER__":
            if not is_processing:
                current_frame = int(values['__FRAME_SLIDER__'])
                
                if selected_marker_idx is not None:
                    min_f = 1
                    max_f = current_frame_count
                    if selected_marker_idx > 0:
                        min_f = video_markers[selected_marker_idx - 1] + 1
                    if selected_marker_idx < len(video_markers) - 1:
                        max_f = video_markers[selected_marker_idx + 1] - 1
                        
                    current_frame = max(min_f, min(current_frame, max_f))
                    video_markers[selected_marker_idx] = current_frame
                    window["__FRAME_SLIDER__"].update(value=current_frame)
                    
                update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count, only_playhead=True)
                window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")
                
                if scrubbing_cap is None:
                    current_filepath = get_selected_filepath(window)
                    if current_filepath and current_filepath[current_filepath.rfind("."):].lower() in VIDEO_TYPES:
                        scrubbing_cap = cv2.VideoCapture(current_filepath)
                
                if scrubbing_cap is not None:
                    update_raw_preview(window, scrubbing_cap, current_frame)
                    
                last_slider_time = time.time()
                pending_slider_update = True
                
        elif event == "__FRAME_SLIDER___RELEASE" or event == "__FRAME_SLIDER___MOUSEUP":
            if scrubbing_cap is not None:
                scrubbing_cap.release()
                scrubbing_cap = None
            playback_frame_number = None

        if pending_slider_update and (time.time() - last_slider_time) > 0.15:
            pending_slider_update = False
            
            if scrubbing_cap is not None:
                scrubbing_cap.release()
                scrubbing_cap = None
            
            if window["__PREVIEW_CANVAS__"].visible and not is_processing:
                show_test_frame(window, values, current_frame)
                update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")

        if event == "__NEW_PROJECT__":
            if check_and_save_current_project(window, current_project_name, file_markers):
                if values.get("__SHOW_PREVIEW__", False) or window["__PREVIEW_CANVAS__"].visible:
                    window["__SHOW_PREVIEW__"].update(False)
                    preview_state["show"] = False
                    window["__PREVIEW_CANVAS__"].update(visible=False)
                    window["__INFO__"].update(visible=True)
                    window["__FRAME_SLIDER__"].update(visible=False)
                    window["__GENERAL_PLAY_CONTAINER__"].update(visible=False)
                    window["__TIMELINE_CONTAINER__"].update(visible=False)
                    window["__LUTS_CONTAINER__"].update(visible=False)
                    
                window["__INPUT_FILE_LIST__"].update(values=[])
                file_markers.clear()
                undo_stack.clear()
                redo_stack.clear()
                update_undo_redo_buttons(window)
                video_markers = []
                selected_marker_idx = None
                gen_play = gen_rew = False
                play_speed = 1
                marker_action_active = None
                update_general_play_buttons(window, gen_play, gen_rew, play_speed)
                window["__STATUS__"].update("New project created")
                current_project_name = None
                window["__MEDIA_FRAME__"].update(value="Media & Export [[empty]]")
                
                try:
                    data_to_save = {
                        "project_name": current_project_name,
                        "files": [],
                        "markers": {},
                        "ops_order": DEFAULT_OPS,
                        "applied_luts": []
                    }
                    with open("list.cfg", "w", encoding="utf-8") as f:
                        json.dump(data_to_save, f)
                except:
                    pass
                # Reset applied LUTs and refresh available list
                window["__APPLIED_LUT_LIST__"].update(values=[])
                window["__LUT_LIST__"].update(values=get_luts_list())

        elif event == "__HELP_BTN__":
            show_help_window()

        if event == "__SAVE_PROJECT__":
            if not current_project_name:
                name = sg.popup_get_text("Enter project name:", title="Save Project")
                if name and name.strip():
                    current_project_name = name.strip()
                    window["__MEDIA_FRAME__"].update(value=f"Media & Export [{current_project_name}]")
            
            if current_project_name:
                os.makedirs("Projects", exist_ok=True)
                project_path = os.path.join("Projects", f"{current_project_name}.cfg")
                try:
                    current_files = window["__INPUT_FILE_LIST__"].get_list_values()
                    data_to_save = {
                        "project_name": current_project_name,
                        "files": current_files,
                        "markers": {f: file_markers[f] for f in current_files if f in file_markers and file_markers[f]},
                        "ops_order": get_action_order(window),
                        "applied_luts": window["__APPLIED_LUT_LIST__"].get_list_values()
                    }
                    with open(project_path, "w", encoding="utf-8") as f:
                        json.dump(data_to_save, f)
                    with open("list.cfg", "w", encoding="utf-8") as f:
                        json.dump(data_to_save, f)
                    window["__STATUS__"].update(f"Project saved as {current_project_name}")
                except Exception as e:
                    sg.popup_error(f"Could not save project: {e}")

        if event == "__LOAD_PROJECT__":
            if check_and_save_current_project(window, current_project_name, file_markers):
                os.makedirs("Projects", exist_ok=True)
                projects = [f for f in os.listdir("Projects") if f.endswith(".cfg")]
                if not projects:
                    sg.popup("No projects found in 'Projects' folder.", title="Load Project")
                else:
                    layout_load = [
                        [sg.Listbox(values=projects, size=(40, 10), key="-PROJ_LIST-", bind_return_key=True)],
                        [sg.Button("Load"), sg.Button("Cancel")]
                    ]
                    win_load = sg.Window("Load Project", layout_load, modal=True)
                    while True:
                        ev_load, val_load = win_load.read()
                        if ev_load in (sg.WIN_CLOSED, "Cancel"):
                            break
                        if ev_load == "Load" or ev_load == "-PROJ_LIST-":
                            if val_load["-PROJ_LIST-"]:
                                selected_proj = val_load["-PROJ_LIST-"][0]
                                proj_path = os.path.join("Projects", selected_proj)
                                try:
                                    with open(proj_path, "r", encoding="utf-8") as f:
                                        data = json.loads(f.read().strip())
                                    
                                    if values.get("__SHOW_PREVIEW__", False) or window["__PREVIEW_CANVAS__"].visible:
                                        window["__SHOW_PREVIEW__"].update(False)
                                        preview_state["show"] = False
                                        window["__PREVIEW_CANVAS__"].update(visible=False)
                                        window["__INFO__"].update(visible=True)
                                        window["__FRAME_SLIDER__"].update(visible=False)
                                        window["__GENERAL_PLAY_CONTAINER__"].update(visible=False)
                                        window["__TIMELINE_CONTAINER__"].update(visible=False)
                                        window["__LUTS_CONTAINER__"].update(visible=False)
                                    
                                    undo_stack.clear()
                                    redo_stack.clear()
                                    update_undo_redo_buttons(window)
                                    video_markers = []
                                    selected_marker_idx = None
                                    gen_play = gen_rew = False
                                    play_speed = 1
                                    marker_action_active = None
                                    update_general_play_buttons(window, gen_play, gen_rew, play_speed)
                                    
                                    current_project_name = data.get("project_name", selected_proj.replace(".cfg", ""))
                                    window["__MEDIA_FRAME__"].update(value=f"Media & Export [{current_project_name}]")
                                    
                                    saved_files = data.get("files", [])
                                    file_markers.clear()
                                    file_markers.update(data.get("markers", {}))
                                    
                                    loaded_applied_luts = [l for l in data.get("applied_luts", []) if os.path.exists(os.path.join(LUTS_DIR, l))]
                                    window["__APPLIED_LUT_LIST__"].update(values=loaded_applied_luts)
                                    window["__LUT_LIST__"].update(values=get_available_luts_list(loaded_applied_luts))
                                    update_action_labels(window)

                                    valid_saved_files = [f for f in saved_files if valid_file(f)]
                                    window["__INPUT_FILE_LIST__"].update(valid_saved_files)
                                    if valid_saved_files:
                                        window["__OUTPUT_FOLDER__"].update(os.path.dirname(valid_saved_files[0]))
                                    
                                    window["__STATUS__"].update(f"Project {current_project_name} loaded")
                                    
                                    with open("list.cfg", "w", encoding="utf-8") as f:
                                        json.dump(data, f)
                                    
                                    if valid_saved_files:
                                        window["__INPUT_FILE_LIST__"].update(set_to_index=0)
                                        window.write_event_value("__INPUT_FILE_LIST__", None)
                                        
                                except Exception as e:
                                    sg.popup_error(f"Could not load project: {e}")
                            break
                    win_load.close()

        if event == "__RESET_SETTINGS__":
            if not is_processing:
                window["__IS_SUFFIX__"].update(True)
                window["__KEEP_SOUND__"].update(True)
                window["__USE_NVENC__"].update(has_nvidia)
                
                window["__USE_CLAHE__"].update(False)
                window["__CLAHE_CLIP__"].update(value=2.0, disabled=True)
                window["__CLAHE_CLIP_TEXT__"].update(text_color='gray')
                
                window["__USE_BRIGHTNESS__"].update(False)
                window["__BRIGHTNESS__"].update(value=0, disabled=True)
                window["__BRIGHTNESS_TEXT__"].update(text_color='gray')
                window["__CONTRAST__"].update(value=1.0, disabled=True)
                window["__CONTRAST_TEXT__"].update(text_color='gray')
                
                window["__USE_SATURATION__"].update(False)
                window["__SATURATION__"].update(value=1.0, disabled=True)
                window["__SATURATION_TEXT__"].update(text_color='gray')
                
                window["__RED_STRENGTH__"].update(value=60)
                window["__HUE_SHIFT__"].update(value=120)
                update_action_labels(window)
                
                window["__STATUS__"].update("Settings reset to default")
                
                if values.get("__SHOW_PREVIEW__", False) and window["__PREVIEW_CANVAS__"].visible:
                    values["__USE_CLAHE__"] = False
                    values["__CLAHE_CLIP__"] = 2.0
                    values["__USE_BRIGHTNESS__"] = False
                    values["__BRIGHTNESS__"] = 0
                    values["__CONTRAST__"] = 1.0
                    values["__USE_SATURATION__"] = False
                    values["__SATURATION__"] = 1.0
                    values["__RED_STRENGTH__"] = 60
                    values["__HUE_SHIFT__"] = 120
                    show_test_frame(window, values, current_frame)
                    update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                    window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")

        if event == "__INPUT_FILES__":

            existing_filepaths = [x for x in window["__INPUT_FILE_LIST__"].get_list_values()]
            new_filepaths = values["__INPUT_FILES__"].split(";")
            
            for f in new_filepaths:
                if f not in existing_filepaths:
                    existing_filepaths.append(f)

            input_filepaths = [f for f in existing_filepaths if valid_file(f)]
            window["__INPUT_FILE_LIST__"].update(input_filepaths)

            if len(input_filepaths) > 0:
                window["__OUTPUT_FOLDER__"].update(os.path.dirname(input_filepaths[0]))
                current_filepath = get_selected_filepath(window)
                if current_filepath:
                    video_markers = file_markers.setdefault(current_filepath, [])
                    undo_stack.clear()
                    redo_stack.clear()
                    update_undo_redo_buttons(window)
                    restore_play_state = None

                    selected_marker_idx = None
                    gen_play = gen_rew = False
                    play_speed = 1
                    marker_action_active = None
                    update_general_play_buttons(window, gen_play, gen_rew, play_speed)
                    update_marker_buttons(window, selected_marker_idx)
                    
                    if scrubbing_cap is not None:
                        scrubbing_cap.release()
                        scrubbing_cap = None

                    window["__SHOW_PREVIEW__"].update(True)
                    preview_state["show"] = True

                    if not is_processing:
                        # Pass None to show_test_frame so it calculates the default frame
                        current_frame, current_frame_count, current_fps = show_test_frame(window, values, None)
                        update_add_button_state(window, video_markers, current_frame, current_fps, current_frame_count)
                        draw_timeline(window, video_markers, selected_marker_idx, current_frame, current_frame_count)
                        window["__STATUS__"].update(f"Frame {current_frame} / {current_frame_count}")

        if event == "__OUTPUT_FOLDER__":
            window["__OUTPUT_FOLDER__"].update(values["__OUTPUT_FOLDER__"])

        if event == "__OPEN_OUTPUT__":
            out_folder = values.get("__OUTPUT_FOLDER__", "./")
            if os.path.exists(out_folder):
                try:
                    os.startfile(os.path.normpath(out_folder))
                except Exception as e:
                    window["__STATUS__"].update(f"Cannot open folder: {e}")
            else:
                window["__STATUS__"].update("Output folder does not exist")

        if event == "__CORRECT__":
            filepaths = [x for x in window["__INPUT_FILE_LIST__"].get_list_values()]
            file_generator = get_tasks(filepaths, file_markers)
            
            set_ui_processing_state(window, True, values)

        if event == "Process":
            selected = window["__INPUT_FILE_LIST__"].get()
            if selected:
                filepaths = [selected[0]]
                file_generator = get_tasks(filepaths, file_markers)
                set_ui_processing_state(window, True, values)


        if event == "__CANCEL__":
            set_ui_processing_state(window, False, values)

            # Close generators so FFmpeg is killed and file handle released before deletion
            if process_video_generator:
                process_video_generator.close()
            if analyze_video_generator:
                analyze_video_generator.close()

            file_generator = None
            analyze_video_generator = None
            process_video_generator = None
            process_start_time = None

            if current_output_filepath and os.path.exists(current_output_filepath):
                time.sleep(0.3)  # let FFmpeg release the file handle
                try:
                    os.remove(current_output_filepath)
                except Exception as e:
                    print(f"Could not delete output file: {e}")
            current_output_filepath = None

            if current_temp_filepath and os.path.exists(current_temp_filepath):
                try: os.remove(current_temp_filepath)
                except: pass
                current_temp_filepath = None

            window["__STATUS__"].update("Cancelled")


        if analyze_video_generator:
            try:
                item = next(analyze_video_generator)
                if type(item) == dict:
                    video_data = item
                    process_video_generator = process_video(video_data, preview_state)
                    current_output_filepath = video_data["output_video_path"]
                    analyze_video_generator = None
                    process_start_time = time.time()
                elif type(item) == tuple:
                    count, frame_count = item
                    now = time.time()
                    if now - last_ui_update_time > 0.5 or count == 1 or count == frame_count:
                        last_ui_update_time = now
                        percent = 100 * count / frame_count if frame_count > 0 else 0
                        
                        if process_start_time and percent > 0:
                            elapsed_time = time.time() - process_start_time
                            total_estimated_time = elapsed_time / (percent / 100)
                            remaining_time = total_estimated_time - elapsed_time
                            
                            if remaining_time < 300:
                                minutes = int(remaining_time // 60)
                                seconds = int(remaining_time % 60)
                                eta_str = f" ({minutes:02d}'{seconds:02d}\" remaining)"
                            else:
                                hours = int(remaining_time // 3600)
                                minutes = int((remaining_time % 3600) // 60)
                                eta_str = f" ({hours:02d}h{minutes:02d}' remaining)"
                        else:
                            eta_str = " (calculating...)"
                            
                        seq_str = f"{current_task['seq']}/{current_task['total_seqs']}" if current_task else "1/1"
                        file_name = os.path.basename(current_task['file']) if current_task else ""
                        status_message = f"Analyzing {seq_str} ({file_name}): {percent:.2f}%{eta_str}"
                        window["__STATUS__"].update(status_message)
                else:
                    pass

            except StopIteration:
                window["__STATUS__"].update("Analysis done")
                analyze_video_generator = None

            except: 
                window["__STATUS__"].update("Analysis failed")
                analyze_video_generator = None

            continue


        if process_video_generator:
            try:
                percent, preview, frame_count = next(process_video_generator)
                
                now = time.time()
                if now - last_ui_update_time > 0.5 or percent >= 100:
                    last_ui_update_time = now
                    
                    if preview and preview_state["show"]:
                        img_arr = np.frombuffer(preview, dtype=np.uint8)
                        img_m = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                        if img_m is not None:
                            draw_preview_on_canvas(window, img_m)
                    elif not preview_state["show"]:
                        window["__PREVIEW_CANVAS__"].update(visible=False)
                    
                    if process_start_time and percent > 0:
                        elapsed_time = time.time() - process_start_time
                        total_estimated_time = elapsed_time / (percent / 100)
                        remaining_time = total_estimated_time - elapsed_time
                        
                        if remaining_time < 300:
                            minutes = int(remaining_time // 60)
                            seconds = int(remaining_time % 60)
                            eta_str = f" ({minutes:02d}'{seconds:02d}\" remaining)"
                        else:
                            hours = int(remaining_time // 3600)
                            minutes = int((remaining_time % 3600) // 60)
                            eta_str = f" ({hours:02d}h{minutes:02d}' remaining)"
                    else:
                        eta_str = " (calculating...)"

                    seq_str = f"{current_task['seq']}/{current_task['total_seqs']}" if current_task else "1/1"
                    file_name = os.path.basename(current_task['file']) if current_task else ""
                    status_message = f"Processing {seq_str} ({file_name}): {percent:.2f}%{eta_str}"
                    window["__STATUS__"].update(status_message)

            except StopIteration:
                window["__STATUS__"].update("Processing done")
                process_video_generator = None
                process_start_time = None
                current_output_filepath = None
                
                if current_temp_filepath and os.path.exists(current_temp_filepath):
                    try: os.remove(current_temp_filepath)
                    except: pass
                    current_temp_filepath = None

            except Exception as e:
                print(f"Processing failed: {e}")
                window["__STATUS__"].update("Processing failed")
                process_video_generator = None
                process_start_time = None
                
                if current_output_filepath and os.path.exists(current_output_filepath):
                    try: os.remove(current_output_filepath)
                    except: pass
                current_output_filepath = None
                    
                if current_temp_filepath and os.path.exists(current_temp_filepath):
                    try: os.remove(current_temp_filepath)
                    except: pass
                    current_temp_filepath = None

                
            continue

        if file_generator:

            try:
                task = next(file_generator)
                current_task = task
                
                filepaths = window["__INPUT_FILE_LIST__"].get_list_values()
                if task["file"] in filepaths:
                    window["__INPUT_FILE_LIST__"].update(set_to_index=filepaths.index(task["file"]))

                name_without_ext = task["name_without_ext"]
                ext = task["ext"]
                
                if task["type"] == "video_segment":
                    name_without_ext = f"{name_without_ext}_{task['seq']}"

                if values["__IS_SUFFIX__"]:
                    new_filename = f"{name_without_ext}_{values['__OUTPUT_PREFIX__']}{ext}"
                else:
                    new_filename = f"{values['__OUTPUT_PREFIX__']}_{name_without_ext}{ext}"
                    
                output_filepath = os.path.join(values["__OUTPUT_FOLDER__"], new_filename)

                counter = 1
                base_new_filename = new_filename
                while os.path.exists(output_filepath):
                    if values["__IS_SUFFIX__"]:
                        new_filename = f"{name_without_ext}_{values['__OUTPUT_PREFIX__']}.{counter}{ext}"
                    else:
                        new_filename = f"{values['__OUTPUT_PREFIX__']}_{name_without_ext}.{counter}{ext}"
                    output_filepath = os.path.join(values["__OUTPUT_FOLDER__"], new_filename)
                    counter += 1

                use_clahe = values.get("__USE_CLAHE__", False)
                clahe_clip = values.get("__CLAHE_CLIP__", 2.0)
                use_saturation = values.get("__USE_SATURATION__", False)
                saturation_val = values.get("__SATURATION__", 1.0)
                use_brightness = values.get("__USE_BRIGHTNESS__", False)
                brightness_val = values.get("__BRIGHTNESS__", 0)
                contrast_val = values.get("__CONTRAST__", 1.0)
                red_str = values.get("__RED_STRENGTH__", 60)
                hue_shift = values.get("__HUE_SHIFT__", 120)
                
                luts = get_selected_lut_arrays(window)
                lut_only = window["__LUT_ONLY_BTN__"].ButtonColor[1] == '#28a745' if hasattr(window["__LUT_ONLY_BTN__"], 'ButtonColor') else False
                ops_order = get_active_action_order(window)
                
                filename_only = os.path.basename(task["file"])
                
                if task["type"] == "image":
                    window["__STATUS__"].update(f"Processing {task['seq']}/{task['total_seqs']} ({filename_only})...")
                    window.refresh()
                    preview = correct_image(task["file"], output_filepath, use_clahe, red_str, hue_shift, clahe_clip, use_saturation, saturation_val, use_brightness, brightness_val, contrast_val, luts, lut_only, ops_order)
                    if preview:
                        img_arr = np.frombuffer(preview, dtype=np.uint8)
                        img_m = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                        if img_m is not None:
                            draw_preview_on_canvas(window, img_m)
                
                elif task["type"] == "video_full":
                    window["__STATUS__"].update(f"Analyzing {task['seq']}/{task['total_seqs']} ({filename_only})...")
                    window.refresh()
                    process_start_time = time.time() 
                    analyze_video_generator = analyze_video(task["file"], output_filepath, keep_sound=values["__KEEP_SOUND__"], use_nvenc=values["__USE_NVENC__"], use_clahe=use_clahe, min_avg_red=red_str, max_hue_shift=hue_shift, clahe_clip=clahe_clip, use_saturation=use_saturation, saturation_val=saturation_val, use_brightness=use_brightness, brightness_val=brightness_val, contrast_val=contrast_val, luts=luts, lut_only=lut_only, ops_order=ops_order, create_demo=values.get("__CREATE_DEMO__", False))
                
                elif task["type"] == "video_segment":
                    window["__STATUS__"].update(f"Analyzing segment {task['seq']}/{task['total_seqs']} ({filename_only})...")
                    window.refresh()
                    process_start_time = time.time() 
                    analyze_video_generator = analyze_video(task["file"], output_filepath, keep_sound=values["__KEEP_SOUND__"], use_nvenc=values["__USE_NVENC__"], use_clahe=use_clahe, min_avg_red=red_str, max_hue_shift=hue_shift, clahe_clip=clahe_clip, use_saturation=use_saturation, saturation_val=saturation_val, use_brightness=use_brightness, brightness_val=brightness_val, contrast_val=contrast_val, start_frame=task["start"], end_frame=task["end"], luts=luts, lut_only=lut_only, ops_order=ops_order, create_demo=values.get("__CREATE_DEMO__", False))

            except StopIteration:
                window["__STATUS__"].update("All done!")
                set_ui_processing_state(window, False, values)
                file_generator = None
                analyze_video_generator = None
                process_video_generator = None
                process_start_time = None
                
                if current_temp_filepath and os.path.exists(current_temp_filepath):
                    try: os.remove(current_temp_filepath)
                    except: pass
                    current_temp_filepath = None

            except Exception as e:
                window["__STATUS__"].update("Error processing files")
                set_ui_processing_state(window, False, values)
                file_generator = None
                analyze_video_generator = None
                process_video_generator = None
                process_start_time = None
                
                if current_temp_filepath and os.path.exists(current_temp_filepath):
                    try: os.remove(current_temp_filepath)
                    except: pass
                    current_temp_filepath = None