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

def _init_graph_state(graph):
    if not hasattr(graph, '_tl_ids'):
        graph._tl_ids = {'bg': None, 'markers': [], 'playhead': None}

def reset_timeline(window):
    """Efface complètement le graph et réinitialise les IDs stockés."""
    graph = window["__TIMELINE__"]
    graph.erase()
    graph._tl_ids = {'bg': None, 'markers': [], 'playhead': None}
    graph._tl_sel_ids = []

def _draw_marker_figures(graph, m, is_start, color, line_w, marker_w):
    ids = []
    ids.append(graph.draw_line((m, 5), (m, 25), color=color, width=line_w))
    if is_start:
        ids.append(graph.draw_line((m, 5), (m + marker_w, 5), color=color, width=line_w))
        ids.append(graph.draw_line((m, 25), (m + marker_w, 25), color=color, width=line_w))
    else:
        ids.append(graph.draw_line((m, 5), (m - marker_w, 5), color=color, width=line_w))
        ids.append(graph.draw_line((m, 25), (m - marker_w, 25), color=color, width=line_w))
    return ids

def draw_timeline(window, markers, selected_idx, current_frame, frame_count, only_playhead=False):
    """Dessine la barre de navigation timeline, les marqueurs et le curseur de lecture (playhead)."""
    graph = window["__TIMELINE__"]
    _init_graph_state(graph)
    ids = graph._tl_ids

    if frame_count <= 0:
        return

    marker_w = max(1, frame_count * 0.010)

    if not hasattr(graph, '_tl_sel_ids'):
        graph._tl_sel_ids = []

    if not only_playhead:
        # Full redraw: update coordinates then replace every figure in place
        window.TKroot.update_idletasks()
        actual_width = graph.Widget.winfo_width()
        if actual_width > 10:
            graph.CanvasSize = (actual_width, 30)

        graph_width = graph.CanvasSize[0] if graph.CanvasSize[0] else 1070
        margin = max(1, int(frame_count * (15 / graph_width) / 2))
        graph.change_coordinates((1 - margin, 0), (frame_count + margin, 30))

        # Background bar
        if ids['bg']:
            graph.delete_figure(ids['bg'])
        ids['bg'] = graph.draw_line((1, 15), (frame_count, 15), color='#444444', width=4)

        # All unselected markers
        for fid in ids['markers']:
            graph.delete_figure(fid)
        ids['markers'] = []

        # Selected marker
        for fid in graph._tl_sel_ids:
            graph.delete_figure(fid)
        graph._tl_sel_ids = []

        for i, m in enumerate(markers):
            color = '#4CAF50' if i % 2 == 0 else '#F44336'
            is_selected = (i == selected_idx)
            if is_selected:
                graph._tl_sel_ids = _draw_marker_figures(graph, m, i % 2 == 0, 'white', 2, marker_w)
            else:
                ids['markers'].extend(_draw_marker_figures(graph, m, i % 2 == 0, color, 1, marker_w))

    else:
        # Lightweight update: only move the selected marker and the playhead
        if selected_idx is not None and selected_idx < len(markers):
            for fid in graph._tl_sel_ids:
                graph.delete_figure(fid)
            m = markers[selected_idx]
            graph._tl_sel_ids = _draw_marker_figures(graph, m, selected_idx % 2 == 0, 'white', 2, marker_w)

    # Playhead
    if ids['playhead']:
        graph.delete_figure(ids['playhead'])
    draw_frame = min(current_frame, frame_count)
    ids['playhead'] = graph.draw_line((draw_frame, 0), (draw_frame, 30), color='white', width=1)
