import os

undo_stack = []
redo_stack = []

def is_frame_inside_sequence(frame, markers):
    """Vérifie si une image donnée se trouve à l'intérieur d'une séquence définie par les marqueurs."""
    for i in range(0, len(markers) - 1, 2):
        start_m = markers[i]
        end_m = markers[i + 1]
        if start_m < frame < end_m:
            return True
    return False

def update_add_button_state(window, markers, current_frame, fps, frame_count):
    """Met à jour l'état d'activation du bouton Ajouter Marqueur."""
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
    """Met à jour l'état des boutons Undo / Redo selon le contenu des piles."""
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

def push_undo_state(window, markers):
    """Ajoute l'état actuel des marqueurs dans la pile d'annulation."""
    global undo_stack, redo_stack
    if not undo_stack or undo_stack[-1] != markers:
        undo_stack.append(list(markers))
        redo_stack.clear()
        update_undo_redo_buttons(window)

def update_marker_buttons(window, selected_idx):
    """Active ou désactive les boutons de navigation et de suppression de marqueurs."""
    has_selection = selected_idx is not None
    can_delete = has_selection and (selected_idx % 2 == 0)

    img_del = os.path.join("assets", "delete.png")
    if os.path.exists(img_del):
        window["__DEL_MARK__"].update(disabled=not can_delete)
    else:
        window["__DEL_MARK__"].update(disabled=not can_delete, button_color=('#555555' if not can_delete else '#dc3545'))

    for k in ["__MARKER_FAST_REW__", "__MARKER_REW__", "__MARKER_FWD__", "__MARKER_FAST_FWD__"]:
        window[k].update(disabled=False)

def draw_timeline(window, markers, selected_idx, current_frame, frame_count, only_playhead=False):
    """Dessine la barre de navigation timeline, les marqueurs et le curseur de lecture (playhead)."""
    graph = window["__TIMELINE__"]

    if not only_playhead:
        graph.erase()
    else:
        if hasattr(graph, '_playhead_id') and graph._playhead_id:
            graph.delete_figure(graph._playhead_id)

    if frame_count <= 0:
        return

    if not only_playhead:
        window.TKroot.update_idletasks()
        actual_width = graph.Widget.winfo_width()
        if actual_width > 10:
            graph.CanvasSize = (actual_width, 30)

        graph_width = graph.CanvasSize[0] if graph.CanvasSize[0] else 1070
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
                # Marqueur de début '['
                graph.draw_line((m, 5), (m, 25), color=outline, width=line_w)
                graph.draw_line((m, 5), (m + marker_w, 5), color=outline, width=line_w)
                graph.draw_line((m, 25), (m + marker_w, 25), color=outline, width=line_w)
            else:
                # Marqueur de fin ']'
                graph.draw_line((m, 5), (m, 25), color=outline, width=line_w)
                graph.draw_line((m, 5), (m - marker_w, 5), color=outline, width=line_w)
                graph.draw_line((m, 25), (m - marker_w, 25), color=outline, width=line_w)

    draw_frame = min(current_frame, frame_count)
    graph._playhead_id = graph.draw_line((draw_frame, 0), (draw_frame, 30), color='white', width=1)
